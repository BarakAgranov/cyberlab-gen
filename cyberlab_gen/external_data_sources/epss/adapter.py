"""The EPSS adapter, its parser, and an ``httpx``-backed client.

``parse_epss_response`` reads the ``api.first.org/data/v1/epss`` JSON shape:
``{"data": [{"cve", "epss", "percentile", "date"}, ...]}`` (scores are returned
as strings). The adapter records an ``EpssRecord`` per scored CVE; an unscored
CVE yields ``None`` (no record, no skip). Never fatal on unavailability (ADR 0042).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cyberlab_gen.errors import ExternalApiRateLimitError, ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import EpssRecord, LookupPriority

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry

_SOURCE_ID = "epss"


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def parse_epss_response(payload: object, *, source_id: str = _SOURCE_ID) -> EpssRecord | None:
    """Parse the first EPSS data row into an ``EpssRecord`` (``None`` when absent/unscored)."""
    if not isinstance(payload, dict):
        return None
    data = cast("dict[object, object]", payload).get("data")
    if not isinstance(data, list) or not data:
        return None
    first = cast("list[object]", data)[0]
    if not isinstance(first, dict):
        return None
    row = {str(k): v for k, v in cast("dict[object, object]", first).items()}
    cve_id = row.get("cve")
    epss = _as_float(row.get("epss"))
    percentile = _as_float(row.get("percentile"))
    if not isinstance(cve_id, str) or epss is None or percentile is None:
        return None
    as_of = row.get("date")
    return EpssRecord(
        source_id=source_id,
        cve_id=cve_id,
        epss=epss,
        percentile=percentile,
        as_of=as_of if isinstance(as_of, str) else None,
    )


@dataclass(slots=True)
class HttpxEpssClient:
    """An ``httpx``-backed ``EpssClient``."""

    client: object  # an httpx.Client
    base_url: str

    def lookup(self, cve_id: str) -> EpssRecord | None:
        """Look up ``cve_id`` against EPSS and parse the response."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxEpssClient.client must be an httpx.Client")
        try:
            response = self.client.get(self.base_url, params={"cve": cve_id})
        except httpx.HTTPError as exc:
            raise ExternalApiUnavailableError(f"EPSS unreachable looking up {cve_id}") from exc
        if response.status_code == 429:
            raise ExternalApiRateLimitError(f"EPSS rate-limited looking up {cve_id}")
        if response.status_code >= 400:
            raise ExternalApiUnavailableError(
                f"EPSS returned {response.status_code} looking up {cve_id}"
            )
        return parse_epss_response(response.json())


@dataclass(slots=True)
class EpssAdapter:
    """Record an EPSS score for each spec CVE that EPSS has scored."""

    source_id: str = _SOURCE_ID
    priority: LookupPriority = LookupPriority.OTHER

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Append an ``EpssRecord`` per scored CVE (one budget-consuming call each)."""
        refs = support.cve_refs(ctx.spec)
        if not refs:
            return
        client = ctx.clients.epss
        if client is None:
            support.record_skip(
                ctx.result,
                field_path="external_references.cves[*]",
                source_id=entry.id,
                reason=support.NOT_INTEGRATED_REASON.format(source_id=entry.id),
            )
            return
        for cve in refs:
            path = f"external_references.cves[{cve.cve_id}]"
            if ctx.budget[0] <= 0:
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=f"external API budget exhausted before {cve.cve_id} EPSS lookup",
                )
                continue
            try:
                record = client.lookup(cve.cve_id)
            except ExternalApiRateLimitError:
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=support.RATE_LIMITED_REASON,
                )
                continue
            except ExternalApiUnavailableError:
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=support.UNAVAILABLE_REASON.format(source_id=entry.id),
                )
                continue
            ctx.budget[0] -= 1
            ctx.result.calls_made += 1
            if record is not None:
                ctx.result.epss_records.append(record)


__all__ = ["EpssAdapter", "HttpxEpssClient", "parse_epss_response"]
