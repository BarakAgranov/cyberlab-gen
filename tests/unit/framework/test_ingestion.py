"""Tests for the Ingestion stage (``cyberlab_gen.framework.ingestion``).

Architectural source: ``pipeline.md §3.2.1``, ``implementation-plan.md §4.2``.

The happy path is exercised against a checked-in pytest-recording cassette
(real ``https://example.com`` fetch, recorded once). The three failure modes
and the cache-hit path use ``httpx.MockTransport`` for deterministic, offline
behavior (ADR 0019). Pure-function normalization and metadata helpers are
tested directly.
"""

from pathlib import Path

import httpx
import pytest

from cyberlab_gen.errors import (
    BotDetectedError,
    IngestionError,
    PaywallError,
    UnreachableUrlError,
)
from cyberlab_gen.framework.ingestion import (
    IngestionConfig,
    compute_content_hash,
    ingest,
    normalize_html,
    read_cached,
    read_cached_text,
)
from cyberlab_gen.providers.retries import RetryStrategy
from cyberlab_gen.schemas import IngestionResult
from cyberlab_gen.state.local_state import LocalState

# A retry strategy with no sleeps so failure-mode tests stay fast.
_NO_SLEEP_RETRIES = RetryStrategy(
    max_attempts=2, base_delay_seconds=0.0, backoff_factor=1.0, jitter_fraction=0.0
)


def _client_serving(handler: object) -> httpx.Client:
    """Build an httpx.Client whose transport runs ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.Client(transport=transport)


# --- normalize_html ---------------------------------------------------------


def test_normalize_html_preserves_heading_structure_as_markers() -> None:
    html = "<h1>Top</h1><p>Body one.</p><h2>Sub</h2><p>Body two.</p>"
    text = normalize_html(html)
    assert "# Top" in text
    assert "## Sub" in text
    # Headings appear before their following body text.
    assert text.index("# Top") < text.index("Body one.") < text.index("## Sub")


def test_normalize_html_drops_script_and_style_content() -> None:
    html = "<style>.a{color:red}</style><script>var secret=1;</script><p>Visible.</p>"
    text = normalize_html(html)
    assert "Visible." in text
    assert "secret" not in text
    assert "color:red" not in text


def test_normalize_html_collapses_whitespace() -> None:
    html = "<p>lots    of\n\n   space\there</p>"
    text = normalize_html(html)
    assert "lots of space here" in text


# --- compute_content_hash ---------------------------------------------------


def test_content_hash_is_stable_sha256_hex() -> None:
    h = compute_content_hash("hello")
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert len(h) == 64


def test_content_hash_changes_with_content() -> None:
    assert compute_content_hash("a") != compute_content_hash("b")


# --- happy path (recorded HTTP) ---------------------------------------------


@pytest.mark.vcr
def test_ingest_records_a_real_blog(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    result = ingest("https://example.com/", state=state)

    assert isinstance(result, IngestionResult)
    assert result.fetch_method == "http_get"
    assert result.publisher_domain == "example.com"
    assert result.word_count > 0
    assert len(result.content_hash) == 64
    # The cache directory and its payloads were written.
    blog_dir = Path(result.cached_path)
    assert blog_dir.is_dir()
    assert (blog_dir / "raw.html").exists()
    assert (blog_dir / "normalized.txt").exists()
    assert (blog_dir / "ingestion.yaml").exists()


# --- cache-then-read: re-ingest reads cache, never re-fetches ----------------


def test_read_cached_returns_the_ingestion_result(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    html = "<html><body><h1>Cached</h1><p>" + ("word " * 50) + "</p></body></html>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    with _client_serving(handler) as client:
        first = ingest("https://blog.example.org/post", state=state, client=client)

    cached = read_cached(first.content_hash, state=state)
    assert cached is not None
    assert cached == first
    assert read_cached_text(first.content_hash, state=state) is not None


def test_read_cached_returns_none_for_unknown_hash(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    assert read_cached("0" * 64, state=state) is None
    assert read_cached_text("0" * 64, state=state) is None


def test_downstream_reads_cache_without_a_second_fetch(tmp_path: Path) -> None:
    """Once ingested, the normalized text is read from cache, not re-fetched."""
    state = LocalState(root=tmp_path)
    html = "<html><body><h1>One Fetch</h1><p>" + ("token " * 40) + "</p></body></html>"
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, html=html)

    with _client_serving(handler) as client:
        result = ingest("https://blog.example.org/once", state=state, client=client)
    assert call_count == 1

    # Downstream read goes to the cache; no client is involved at all.
    text = read_cached_text(result.content_hash, state=state)
    assert text is not None
    assert "# One Fetch" in text
    assert call_count == 1  # unchanged — no re-fetch


# --- failure modes (never bypassed) -----------------------------------------


def test_unreachable_url_raises_with_clear_message(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed")

    config = IngestionConfig(retry_strategy=_NO_SLEEP_RETRIES)
    with _client_serving(handler) as client:  # noqa: SIM117 -- two distinct context concerns
        with pytest.raises(UnreachableUrlError) as exc:
            ingest("https://nope.invalid/x", state=state, client=client, config=config)
    assert "Could not fetch" in str(exc.value)
    assert exc.value.url == "https://nope.invalid/x"
    assert exc.value.stage == "ingestion"


def test_paywall_403_raises_paywall_error(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, html="<html><body>Forbidden</body></html>")

    with _client_serving(handler) as client:  # noqa: SIM117
        with pytest.raises(PaywallError) as exc:
            ingest("https://paywalled.example.com/article", state=state, client=client)
    assert "paywall" in str(exc.value).lower()
    assert "bypass" in str(exc.value).lower()


def test_very_short_body_raises_paywall_error(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Subscribe to read.")

    with _client_serving(handler) as client:  # noqa: SIM117
        with pytest.raises(PaywallError) as exc:
            ingest("https://stub.example.com/article", state=state, client=client)
    assert "short" in str(exc.value).lower()


def test_cloudflare_interstitial_raises_bot_detected_error(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    interstitial = (
        "<html><head><title>Just a moment...</title></head><body>"
        "<div class='cf-browser-verification'>Checking your browser before "
        "accessing the site. DDoS protection by Cloudflare.</div>"
        "<div id='cf-challenge-running'></div></body></html>"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, html=interstitial, headers={"server": "cloudflare"})

    with _client_serving(handler) as client:  # noqa: SIM117
        with pytest.raises(BotDetectedError) as exc:
            ingest("https://protected.example.com/post", state=state, client=client)
    assert "bot" in str(exc.value).lower()
    assert "captcha" in str(exc.value).lower() or "bot detection" in str(exc.value).lower()


def test_bot_detection_is_checked_before_paywall(tmp_path: Path) -> None:
    """A Cloudflare 403 interstitial is a bot block, not a paywall."""
    state = LocalState(root=tmp_path)
    interstitial = (
        "<html><body>Please complete the security check to access "
        "the site. cf-challenge</body></html>"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, html=interstitial, headers={"server": "cloudflare"})

    with _client_serving(handler) as client:  # noqa: SIM117
        with pytest.raises(BotDetectedError):
            ingest("https://protected.example.com/x", state=state, client=client)


def test_server_error_retries_then_unreachable(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="upstream down")

    config = IngestionConfig(retry_strategy=_NO_SLEEP_RETRIES)
    with _client_serving(handler) as client:  # noqa: SIM117
        with pytest.raises(UnreachableUrlError) as exc:
            ingest("https://flaky.example.com/x", state=state, client=client, config=config)
    assert attempts == _NO_SLEEP_RETRIES.max_attempts  # retried per §3.7
    assert "503" in str(exc.value)


def test_transient_then_success(tmp_path: Path) -> None:
    """A transient 503 followed by a 200 succeeds within the retry budget."""
    state = LocalState(root=tmp_path)
    html = "<html><body><h1>Recovered</h1><p>" + ("word " * 60) + "</p></body></html>"
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="warming up")
        return httpx.Response(200, html=html)

    config = IngestionConfig(retry_strategy=_NO_SLEEP_RETRIES)
    with _client_serving(handler) as client:
        result = ingest("https://flaky.example.com/ok", state=state, client=client, config=config)
    assert attempts == 2
    assert result.word_count > 0


# --- failure-mode subtypes share the IngestionError base --------------------


@pytest.mark.parametrize(
    "subtype",
    [UnreachableUrlError, PaywallError, BotDetectedError],
)
def test_failure_subtypes_are_ingestion_errors(subtype: type[IngestionError]) -> None:
    err = subtype("x", url="https://e.example.com")
    assert isinstance(err, IngestionError)
    assert err.stage == "ingestion"
    assert err.url == "https://e.example.com"


# --- metadata ---------------------------------------------------------------


def test_metadata_records_canonical_url_after_redirect(tmp_path: Path) -> None:
    state = LocalState(root=tmp_path)
    html = "<html><body><h1>Final</h1><p>" + ("text " * 50) + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://news.example.com/new"})
        return httpx.Response(200, html=html)

    with _client_serving(handler) as client:
        result = ingest("https://news.example.com/old", state=state, client=client)
    assert str(result.url) == "https://news.example.com/old"
    assert str(result.canonical_url) == "https://news.example.com/new"
    assert result.publisher_domain == "news.example.com"
