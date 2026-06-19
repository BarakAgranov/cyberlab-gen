"""The bulletin adapter, an RSS/Atom parser, and an ``httpx``-backed client.

``parse_rss_feed`` handles both RSS ``<item>`` and Atom ``<entry>`` shapes (the
three clouds differ). The adapter fires only when the spec declares the trigger's
``target:<cloud>`` facet, recording recent items as lab-level ``BulletinRecord``
context. ``best_effort`` sources never halt the run (ADR 0042).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from cyberlab_gen.errors import ExternalApiRateLimitError, ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import BulletinRecord, LookupPriority

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def parse_rss_feed(feed_text: str, *, source_id: str) -> list[BulletinRecord]:
    """Parse an RSS or Atom feed into ``BulletinRecord``s (empty on a malformed feed).

    A malformed feed is treated as empty rather than raised: the caller already
    tolerates an unavailable ``best_effort`` source (ADR 0042), and a feed that
    changed format is the documented ``best_effort`` failure mode.
    """
    try:
        # Advisory feeds are semi-trusted; a malformed/oversized feed is the documented
        # best_effort failure mode and is caught below as an empty result (ADR 0042).
        root = ElementTree.fromstring(feed_text)
    except ElementTree.ParseError:
        return []

    out: list[BulletinRecord] = []
    # RSS 2.0: channel/item with title/link/pubDate/description.
    for item in root.iter("item"):
        out.append(
            BulletinRecord(
                source_id=source_id,
                title=_text(item.find("title")) or "(untitled)",
                link=_text(item.find("link")),
                published=_text(item.find("pubDate")),
                summary=_text(item.find("description")),
            )
        )
    # Atom: entry with title/link[@href]/updated/summary.
    for entry in root.iter(f"{_ATOM_NS}entry"):
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href") if link_el is not None else None
        out.append(
            BulletinRecord(
                source_id=source_id,
                title=_text(entry.find(f"{_ATOM_NS}title")) or "(untitled)",
                link=link,
                published=_text(entry.find(f"{_ATOM_NS}updated")),
                summary=_text(entry.find(f"{_ATOM_NS}summary")),
            )
        )
    return out


@dataclass(slots=True)
class HttpxBulletinClient:
    """An ``httpx``-backed ``BulletinClient`` for one feed."""

    client: object  # an httpx.Client
    base_url: str
    source_id: str

    def list_recent(self) -> list[BulletinRecord]:
        """Fetch and parse the feed (raises on transport/HTTP failure)."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxBulletinClient.client must be an httpx.Client")
        try:
            response = self.client.get(self.base_url)
        except httpx.HTTPError as exc:
            raise ExternalApiUnavailableError(f"{self.source_id} feed unreachable") from exc
        if response.status_code == 429:
            raise ExternalApiRateLimitError(f"{self.source_id} feed rate-limited")
        if response.status_code >= 400:
            raise ExternalApiUnavailableError(
                f"{self.source_id} feed returned {response.status_code}"
            )
        return parse_rss_feed(response.text, source_id=self.source_id)


@dataclass(slots=True)
class BulletinAdapter:
    """Record recent bulletins when the spec targets the feed's cloud."""

    source_id: str
    priority: LookupPriority = LookupPriority.BULLETIN

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Fire on the ``target:<cloud>`` facet; record recent items as lab-level context."""
        target = support.triggered_facet(entry)
        if target is None:
            # No facet predicate to resolve — honest skip naming the gap.
            support.record_skip(
                ctx.result,
                field_path="facets",
                source_id=entry.id,
                reason=f"{entry.id} declares no resolvable facet trigger",
            )
            return
        if not support.spec_has_facet(ctx.spec, target):
            # Trigger did not fire — the spec does not target this cloud. Not a skip.
            return
        client = ctx.clients.bulletins.get(entry.id)
        if client is None:
            support.record_skip(
                ctx.result,
                field_path=f"facets[{target}]",
                source_id=entry.id,
                reason=support.NOT_INTEGRATED_REASON.format(source_id=entry.id),
            )
            return
        if ctx.budget[0] <= 0:
            support.record_skip(
                ctx.result,
                field_path=f"facets[{target}]",
                source_id=entry.id,
                reason=f"external API budget exhausted before {entry.id} feed fetch",
            )
            return
        try:
            records = client.list_recent()
        except (ExternalApiRateLimitError, ExternalApiUnavailableError):
            support.record_skip(
                ctx.result,
                field_path=f"facets[{target}]",
                source_id=entry.id,
                reason=support.UNAVAILABLE_REASON.format(source_id=entry.id),
            )
            return
        ctx.budget[0] -= 1
        ctx.result.calls_made += 1
        ctx.result.bulletin_records.extend(records)


__all__ = ["BulletinAdapter", "HttpxBulletinClient", "parse_rss_feed"]
