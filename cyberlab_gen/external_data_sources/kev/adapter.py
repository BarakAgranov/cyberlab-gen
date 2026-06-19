"""The CISA KEV adapter, its parser, and an ``httpx``-backed client.

``parse_kev_catalog`` models the published KEV catalog JSON losslessly for the
documented fields (``registry-details.md §4.2``). The adapter records a
``KevRecord`` for each spec CVE that is KEV-listed; a not-listed CVE is a valid
answer (no record, no skip). Source unavailability is never fatal (ADR 0042).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cyberlab_gen.errors import ExternalApiRateLimitError, ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import KevRecord, LookupPriority

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry

_SOURCE_ID = "cisa_kev"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_kev_catalog(payload: object, *, source_id: str = _SOURCE_ID) -> dict[str, KevRecord]:
    """Parse the KEV catalog JSON into a ``{cve_id: KevRecord}`` index.

    Catalog shape: ``{"vulnerabilities": [{"cveID", "vendorProject", "product",
    "vulnerabilityName", "dateAdded", "shortDescription", "requiredAction",
    "dueDate", "knownRansomwareCampaignUse", "notes", "cwes": [...]}, ...]}``.
    """
    out: dict[str, KevRecord] = {}
    if not isinstance(payload, dict):
        return out
    raw = cast("dict[object, object]", payload)
    vulns = raw.get("vulnerabilities")
    if not isinstance(vulns, list):
        return out
    for item in cast("list[object]", vulns):
        if not isinstance(item, dict):
            continue
        entry = {str(k): v for k, v in cast("dict[object, object]", item).items()}
        cve_id = _str_or_none(entry.get("cveID"))
        if cve_id is None:
            continue
        cwes_raw = entry.get("cwes")
        cwes: list[str] = []
        if isinstance(cwes_raw, list):
            cwes = [c for c in cast("list[object]", cwes_raw) if isinstance(c, str)]
        out[cve_id] = KevRecord(
            source_id=source_id,
            cve_id=cve_id,
            vendor_project=_str_or_none(entry.get("vendorProject")),
            product=_str_or_none(entry.get("product")),
            vulnerability_name=_str_or_none(entry.get("vulnerabilityName")),
            date_added=_str_or_none(entry.get("dateAdded")),
            short_description=_str_or_none(entry.get("shortDescription")),
            required_action=_str_or_none(entry.get("requiredAction")),
            due_date=_str_or_none(entry.get("dueDate")),
            known_ransomware_campaign_use=_str_or_none(entry.get("knownRansomwareCampaignUse")),
            notes=_str_or_none(entry.get("notes")),
            cwes=cwes,
        )
    return out


@dataclass(slots=True)
class HttpxKevClient:
    """An ``httpx``-backed ``KevClient`` (downloads the catalog once, queries locally)."""

    client: object  # an httpx.Client
    base_url: str
    _index: dict[str, KevRecord] | None = None

    def lookup(self, cve_id: str) -> KevRecord | None:
        """Return the KEV entry for ``cve_id`` or ``None`` when not listed."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxKevClient.client must be an httpx.Client")
        if self._index is None:
            try:
                response = self.client.get(self.base_url)
            except httpx.HTTPError as exc:
                raise ExternalApiUnavailableError("CISA KEV catalog unreachable") from exc
            if response.status_code == 429:
                raise ExternalApiRateLimitError("CISA KEV rate-limited")
            if response.status_code >= 400:
                raise ExternalApiUnavailableError(f"CISA KEV returned {response.status_code}")
            self._index = parse_kev_catalog(response.json())
        return self._index.get(cve_id)


@dataclass(slots=True)
class KevAdapter:
    """Record KEV inclusion for each spec CVE listed in the catalog."""

    source_id: str = _SOURCE_ID
    priority: LookupPriority = LookupPriority.OTHER

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Append a ``KevRecord`` for each KEV-listed CVE (downloaded once, queried locally)."""
        refs = support.cve_refs(ctx.spec)
        if not refs:
            return
        client = ctx.clients.kev
        if client is None:
            support.record_skip(
                ctx.result,
                field_path="external_references.cves[*]",
                source_id=entry.id,
                reason=support.NOT_INTEGRATED_REASON.format(source_id=entry.id),
            )
            return
        if ctx.budget[0] <= 0:
            support.record_skip(
                ctx.result,
                field_path="external_references.cves[*]",
                source_id=entry.id,
                reason="external API budget exhausted before KEV catalog fetch",
            )
            return
        # KEV is downloaded once and queried locally — one budget unit total.
        ctx.budget[0] -= 1
        ctx.result.calls_made += 1
        for cve in refs:
            try:
                record = client.lookup(cve.cve_id)
            except ExternalApiRateLimitError:
                support.record_skip(
                    ctx.result,
                    field_path=f"external_references.cves[{cve.cve_id}]",
                    source_id=entry.id,
                    reason=support.RATE_LIMITED_REASON,
                )
                continue
            except ExternalApiUnavailableError:
                support.record_skip(
                    ctx.result,
                    field_path=f"external_references.cves[{cve.cve_id}]",
                    source_id=entry.id,
                    reason=support.UNAVAILABLE_REASON.format(source_id=entry.id),
                )
                continue
            if record is not None:
                ctx.result.kev_records.append(record)


__all__ = ["HttpxKevClient", "KevAdapter", "parse_kev_catalog"]
