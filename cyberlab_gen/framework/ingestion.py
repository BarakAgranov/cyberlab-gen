"""Ingestion stage — fetch, normalize, hash, cache a blog URL.

Architectural source: ``pipeline.md`` §3.2.1 (responsibilities + failure
modes) and ``implementation-plan.md`` §4.2 ("Ingestion stage").

This is deterministic framework code, not an agent (``architecture.md`` §1.5):
no LLM is involved. The stage fetches a URL with a bounded timeout, normalizes
the HTML to text while preserving heading structure as markers, computes a
SHA-256 of the normalized text, writes both the raw and normalized payloads to
``~/.cyberlab-gen/cache/<blog-hash>/``, and records the structural metadata in
an ``IngestionResult``. Downstream stages read from the cache and never
re-fetch (``pipeline.md`` §3.2.1: protects against the blog changing
mid-pipeline).

Scope discipline (``pipeline.md`` §3.2.1): content-quality and in-scope
judgment is the Extractor's sole job and is deliberately absent here. Ingestion
only decides *can I read this at all* — unreachable, paywalled, or bot-blocked
URLs fail with a clear message and are *never* bypassed (CLAUDE.md hard rule;
``implementation-plan.md`` §4.6 risks).

Transient fetch failures retry with exponential backoff per ``pipeline.md``
§3.7 (the same 3-attempt strategy the provider layer uses, reusing
``providers.retries.TRANSIENT_RETRIES`` for the parameters).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from cyberlab_gen.errors import (
    BotDetectedError,
    PaywallError,
    UnreachableUrlError,
)
from cyberlab_gen.providers.retries import TRANSIENT_RETRIES, RetryStrategy
from cyberlab_gen.schemas.ingestion import IngestionResult
from cyberlab_gen.state.local_state import LocalState

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Default fetch timeout. ``implementation-plan.md`` §4.2: 10s, configurable.
DEFAULT_TIMEOUT_SECONDS: float = 10.0

#: HTTP status constants used for failure classification. Plain ints (not
#: ``httpx.codes`` members, which are tuple-valued enum entries that don't
#: compare equal to a bare ``int`` under strict typing).
_HTTP_FORBIDDEN: int = 403
_HTTP_TOO_MANY_REQUESTS: int = 429
_HTTP_SERVER_ERROR_MIN: int = 500
_HTTP_SERVER_ERROR_MAX: int = 600
_HTTP_CLIENT_ERROR_MIN: int = 400

#: Bodies at or below this many characters (after whitespace trimming) are
#: treated as paywall/stub responses. ``implementation-plan.md`` §4.2 names
#: "very-short body" as a paywall signal; a real technical writeup is far
#: longer than this. Conservative so we never misclassify a real (if terse)
#: post — the cost of a false negative here is only that the Extractor sees a
#: thin blog and flags low completeness (``pipeline.md`` §3.2.2), which is the
#: correct owner of that judgment.
PAYWALL_BODY_CHAR_THRESHOLD: int = 200

#: Substrings whose presence in a response body marks a bot-detection /
#: anti-automation interstitial. Matched case-insensitively. These are the
#: stable markers Cloudflare and common WAFs emit on their challenge pages;
#: they appear in the interstitial HTML, never in a real writeup's prose.
_BOT_INTERSTITIAL_MARKERS: tuple[str, ...] = (
    "cf-browser-verification",
    "cf-challenge",
    "challenge-platform",
    "/cdn-cgi/challenge-platform",
    "checking your browser before accessing",
    "attention required! | cloudflare",
    "ddos protection by cloudflare",
    "enable javascript and cookies to continue",
    "verify you are human",
    "please complete the security check to access",
)

#: HTML tags whose text content is never part of the readable article.
_SKIP_TEXT_TAGS: frozenset[str] = frozenset({"script", "style", "head", "title", "noscript"})

#: Heading tags, mapped to the markdown-style prefix used to preserve their
#: level as a marker in the normalized text (``implementation-plan.md`` §4.2:
#: "preserve heading structure as markers"). The Extractor uses these for
#: narrative granularity (``pipeline.md`` §3.2.1).
_HEADING_PREFIXES: Mapping[str, str] = {
    "h1": "# ",
    "h2": "## ",
    "h3": "### ",
    "h4": "#### ",
    "h5": "##### ",
    "h6": "###### ",
}

#: Block-level tags that force a paragraph break in the normalized text.
_BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "ul",
        "ol",
        "li",
        "pre",
        "blockquote",
        "table",
        "tr",
        "br",
        "hr",
    }
)


@dataclass(frozen=True)
class IngestionConfig:
    """Tunable ingestion parameters.

    ``timeout_seconds`` is the fetch timeout (``implementation-plan.md`` §4.2:
    10s default, configurable). ``retry_strategy`` controls transient-failure
    backoff (``pipeline.md`` §3.7); defaults to the shared transient strategy.
    ``user_agent`` is sent on the request so publishers can identify the tool.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retry_strategy: RetryStrategy = TRANSIENT_RETRIES
    user_agent: str = "cyberlab-gen/0.0.1 (+https://github.com/cyberlab-gen)"


class _TextExtractor(HTMLParser):
    """Convert HTML to text, preserving heading structure as markdown markers.

    Stdlib-only (``html.parser``) per the brief's library discretion — no
    third-party HTML dependency is added for Phase 1. Heading tags emit a
    ``#``-prefixed line so the Extractor can recover the document's narrative
    granularity; other block tags emit paragraph breaks; inline runs of
    whitespace collapse to single spaces.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._heading_prefix: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
            return
        if tag in _HEADING_PREFIXES:
            self._parts.append("\n\n")
            self._heading_prefix = _HEADING_PREFIXES[tag]
            self._parts.append(self._heading_prefix)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _HEADING_PREFIXES:
            self._heading_prefix = None
            self._parts.append("\n\n")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        collapsed = " ".join(data.split())
        if collapsed:
            self._parts.append(collapsed + " ")

    def get_text(self) -> str:
        """Return the normalized text: trimmed, with collapsed blank lines."""
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.split("\n")]
        out: list[str] = []
        blank_run = 0
        for line in lines:
            if line:
                out.append(line)
                blank_run = 0
            else:
                blank_run += 1
                if blank_run == 1:
                    out.append("")
        return "\n".join(out).strip()


def normalize_html(html: str) -> str:
    """Normalize raw HTML into heading-preserving plain text.

    ``implementation-plan.md`` §4.2: HTML → text, preserving heading structure
    as markers. Headings become ``#``-prefixed lines; block elements become
    paragraph breaks; ``script``/``style``/``head`` content is dropped.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def compute_content_hash(text: str) -> str:
    """SHA-256 hex digest of the normalized text (``implementation-plan.md`` §4.2)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    """Whitespace-delimited word count of the normalized text."""
    return len(text.split())


def _publisher_domain(url: str) -> str:
    """Host portion of ``url`` (``implementation-plan.md`` §4.2 metadata field)."""
    host = urlparse(url).hostname
    if not host:
        # urlparse on a validated http(s) URL always yields a host; this guard
        # only fires on a degenerate input and keeps the field non-empty.
        return url
    return host


def _looks_like_bot_interstitial(headers: httpx.Headers, body: str) -> bool:
    """True when the response is a bot-detection challenge, not real content.

    Checks the stable Cloudflare/WAF response header and a set of interstitial
    body markers. Detection only — the obstacle is never bypassed.
    """
    server = headers.get("server", "").lower()
    body_lower = body.lower()
    if "cloudflare" in server and any(m in body_lower for m in _BOT_INTERSTITIAL_MARKERS):
        return True
    return any(marker in body_lower for marker in _BOT_INTERSTITIAL_MARKERS)


def _classify_failure(response: httpx.Response, url: str) -> None:
    """Raise the right ``IngestionError`` subtype for a non-readable response.

    Returns ``None`` when the response looks readable. The order matters:
    bot-detection is checked before paywall because a Cloudflare 403
    interstitial is a bot block, not a paywall, and the messages differ.
    Never attempts to bypass any obstacle (CLAUDE.md hard rule).
    """
    body = response.text
    if _looks_like_bot_interstitial(response.headers, body):
        raise BotDetectedError(
            f"Bot-detection interstitial returned for {url} "
            f"(HTTP {response.status_code}). cyberlab-gen does not bypass bot "
            f"detection or CAPTCHAs; fetch the page manually and pass the saved "
            f"content, or choose a different source.",
            url=url,
        )
    if response.status_code == _HTTP_FORBIDDEN:
        raise PaywallError(
            f"Access forbidden (HTTP 403) for {url}. This usually means the "
            f"content is paywalled or access-restricted. cyberlab-gen does not "
            f"bypass paywalls; choose a freely readable source.",
            url=url,
        )
    if len(body.strip()) <= PAYWALL_BODY_CHAR_THRESHOLD:
        raise PaywallError(
            f"Response body for {url} is too short "
            f"({len(body.strip())} chars) to be a readable writeup; this is a "
            f"common paywall/stub signal. cyberlab-gen does not bypass "
            f"paywalls; choose a freely readable source.",
            url=url,
        )


def _fetch(url: str, config: IngestionConfig, client: httpx.Client) -> httpx.Response:
    """Fetch ``url`` with bounded timeout and transient-failure retry.

    Transient conditions (connect/read timeouts, transient transport errors,
    5xx, 429) retry with exponential backoff per ``pipeline.md`` §3.7. After
    the retry budget is exhausted the URL is treated as unreachable. Non-2xx
    responses that are *not* retryable (e.g. 403, 404) return so the caller can
    classify them (403 → paywall; others → unreachable).
    """
    strategy = config.retry_strategy
    last_exc: Exception | None = None
    last_status: int | None = None
    for attempt in range(1, strategy.max_attempts + 1):
        try:
            response = client.get(
                url,
                timeout=config.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": config.user_agent},
            )
        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning(
                "ingestion fetch timeout for %s (attempt %d/%d)",
                url,
                attempt,
                strategy.max_attempts,
            )
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning(
                "ingestion transport error for %s (attempt %d/%d): %s",
                url,
                attempt,
                strategy.max_attempts,
                exc,
            )
        else:
            if response.status_code == _HTTP_TOO_MANY_REQUESTS or (
                _HTTP_SERVER_ERROR_MIN <= response.status_code < _HTTP_SERVER_ERROR_MAX
            ):
                last_status = response.status_code
                last_exc = None
                logger.warning(
                    "ingestion got retryable status %d for %s (attempt %d/%d)",
                    response.status_code,
                    url,
                    attempt,
                    strategy.max_attempts,
                )
            else:
                return response
        if attempt < strategy.max_attempts:
            delay = strategy.base_delay_seconds * (strategy.backoff_factor ** (attempt - 1))
            if delay > 0:
                time.sleep(delay)
    detail = f"HTTP {last_status}" if last_status is not None else str(last_exc)
    raise UnreachableUrlError(
        f"Could not fetch {url} after {strategy.max_attempts} attempts ({detail}).",
        url=url,
        cause=last_exc,
    )


def ingest(
    url: str,
    *,
    config: IngestionConfig | None = None,
    state: LocalState | None = None,
    client: httpx.Client | None = None,
) -> IngestionResult:
    """Fetch, normalize, hash, and cache ``url``; return its ``IngestionResult``.

    ``pipeline.md`` §3.2.1 / ``implementation-plan.md`` §4.2. On a fetch that
    is unreadable (unreachable, paywalled, bot-blocked) raises the matching
    ``IngestionError`` subtype with a clear message and never bypasses the
    obstacle (CLAUDE.md hard rule).

    Cache-then-read discipline: the raw and normalized payloads plus the
    ``IngestionResult`` are written under ``<cache>/<content-hash>/``. Re-fetch
    protection is handled by ``ingest_cached`` / ``read_cached``, which read an
    existing cache entry rather than fetching again.

    ``client`` is injectable so callers (and tests) can supply a configured or
    transport-backed ``httpx.Client``; when omitted a default client is created
    and closed per call.
    """
    config = config or IngestionConfig()
    state = state or LocalState()
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = _fetch(url, config, client)
        # Classify obstacle responses (bot interstitial, 403 paywall, very-short
        # body) before treating the payload as readable content. Never bypassed.
        _classify_failure(response, url)
        if response.status_code >= _HTTP_CLIENT_ERROR_MIN:
            raise UnreachableUrlError(
                f"Could not fetch {url} (HTTP {response.status_code}).",
                url=url,
            )
        raw_html = response.text
        canonical_url = str(response.url)
        fetched_at = datetime.now(UTC)
    finally:
        if owns_client:
            client.close()

    normalized = normalize_html(raw_html)
    content_hash = compute_content_hash(normalized)
    blog_dir = state.cache_dir / content_hash
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "raw.html").write_text(raw_html, encoding="utf-8")
    (blog_dir / "normalized.txt").write_text(normalized, encoding="utf-8")

    result = IngestionResult(
        url=url,  # type: ignore[arg-type]  # validated to HttpUrl by the model
        canonical_url=canonical_url,  # type: ignore[arg-type]
        content_hash=content_hash,
        fetched_at=fetched_at,
        fetch_method="http_get",
        word_count=_word_count(normalized),
        publisher_domain=_publisher_domain(canonical_url),
        cached_path=str(blog_dir),
    )
    (blog_dir / "ingestion.yaml").write_text(result.to_yaml(), encoding="utf-8")
    logger.info(
        "ingested %s -> %s (%d words, hash %s)",
        url,
        canonical_url,
        result.word_count,
        content_hash,
    )
    return result


def read_cached(content_hash: str, *, state: LocalState | None = None) -> IngestionResult | None:
    """Return the cached ``IngestionResult`` for ``content_hash``, or ``None``.

    ``pipeline.md`` §3.2.1: downstream stages read from the cache and never
    re-fetch. This is the read side of that discipline.
    """
    state = state or LocalState()
    manifest = state.cache_dir / content_hash / "ingestion.yaml"
    if not manifest.exists():
        return None
    return IngestionResult.from_yaml(manifest.read_text(encoding="utf-8"))


def read_cached_text(content_hash: str, *, state: LocalState | None = None) -> str | None:
    """Return the cached normalized text for ``content_hash``, or ``None``.

    The Extractor consumes this normalized text; it reads from cache rather
    than re-fetching (``pipeline.md`` §3.2.1).
    """
    state = state or LocalState()
    text_path = state.cache_dir / content_hash / "normalized.txt"
    if not text_path.exists():
        return None
    return text_path.read_text(encoding="utf-8")


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "IngestionConfig",
    "compute_content_hash",
    "ingest",
    "normalize_html",
    "read_cached",
    "read_cached_text",
]
