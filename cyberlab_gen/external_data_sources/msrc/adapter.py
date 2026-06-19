"""The MSRC adapter, its CVRF parser, and an ``httpx``-backed client.

``parse_msrc_cvrf`` reads the documented CVRF JSON nesting: ``ProductTree``
(ProductID → product name), and per-``Vulnerability`` ``Title`` /
``ProductStatuses`` (affected ProductIDs) / ``Remediations`` (fix versions) /
``CVSSScoreSets``. It resolves ProductIDs to names and captures the affected-
product + remediation (fix-version) detail the materiality rules name. Never fatal
on unavailability (ADR 0042).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cyberlab_gen.errors import ExternalApiRateLimitError, ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import (
    LookupPriority,
    MsrcRecord,
    MsrcRemediation,
)

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry

_SOURCE_ID = "msrc"


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in cast("dict[object, object]", value).items()}
    return {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(cast("list[object]", value))
    return []


def _value_of(node: object) -> str | None:
    """CVRF wraps many scalars as ``{"Value": ...}``; pull the string out."""
    if isinstance(node, str):
        return node
    inner = _as_dict(node).get("Value")
    return inner if isinstance(inner, str) else None


def _product_map(payload: dict[str, object]) -> dict[str, str]:
    """ProductID → product name, from ``ProductTree.FullProductName``."""
    out: dict[str, str] = {}
    for item in _as_list(_as_dict(payload.get("ProductTree")).get("FullProductName")):
        node = _as_dict(item)
        pid = node.get("ProductID")
        name = node.get("Value")
        if isinstance(pid, str) and isinstance(name, str):
            out[pid] = name
    return out


def _resolve_products(product_ids: list[object], names: dict[str, str]) -> list[str]:
    out: list[str] = []
    for pid in product_ids:
        if isinstance(pid, str):
            out.append(names.get(pid, pid))
    return out


def parse_msrc_cvrf(
    payload: object, cve_id: str, *, source_id: str = _SOURCE_ID
) -> MsrcRecord | None:
    """Parse the CVRF document for ``cve_id`` into an ``MsrcRecord`` (``None`` when absent)."""
    doc = _as_dict(payload)
    if not doc:
        return None
    names = _product_map(doc)
    for vuln_obj in _as_list(doc.get("Vulnerability")):
        vuln = _as_dict(vuln_obj)
        if vuln.get("CVE") != cve_id:
            continue

        affected: list[str] = []
        for status in _as_list(vuln.get("ProductStatuses")):
            affected.extend(_resolve_products(_as_list(_as_dict(status).get("ProductID")), names))

        remediations: list[MsrcRemediation] = []
        for rem_obj in _as_list(vuln.get("Remediations")):
            rem = _as_dict(rem_obj)
            products = _resolve_products(_as_list(rem.get("ProductID")), names)
            url = rem.get("URL")
            fixed = rem.get("FixedBuild")
            remediations.append(
                MsrcRemediation(
                    product=products[0] if products else None,
                    fixed_build=fixed if isinstance(fixed, str) else None,
                    description=_value_of(rem.get("Description")),
                    url=url if isinstance(url, str) else None,
                )
            )

        score: float | None = None
        vector: str | None = None
        score_sets = _as_list(vuln.get("CVSSScoreSets"))
        if score_sets:
            first = _as_dict(score_sets[0])
            raw_score = first.get("BaseScore")
            if isinstance(raw_score, (int, float)):
                score = float(raw_score)
            raw_vector = first.get("Vector")
            vector = raw_vector if isinstance(raw_vector, str) else None

        return MsrcRecord(
            source_id=source_id,
            cve_id=cve_id,
            title=_value_of(vuln.get("Title")),
            affected_products=list(dict.fromkeys(affected)),
            remediations=remediations,
            cvss_score=score,
            cvss_vector=vector,
        )
    return None


@dataclass(slots=True)
class HttpxMsrcClient:
    """An ``httpx``-backed ``MsrcClient``."""

    client: object  # an httpx.Client
    base_url: str

    def lookup(self, cve_id: str) -> MsrcRecord | None:
        """Fetch the CVRF document and parse the entry for ``cve_id``."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxMsrcClient.client must be an httpx.Client")
        try:
            response = self.client.get(f"{self.base_url}/cvrf/{cve_id}")
        except httpx.HTTPError as exc:
            raise ExternalApiUnavailableError(f"MSRC unreachable looking up {cve_id}") from exc
        if response.status_code == 429:
            raise ExternalApiRateLimitError(f"MSRC rate-limited looking up {cve_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ExternalApiUnavailableError(
                f"MSRC returned {response.status_code} looking up {cve_id}"
            )
        return parse_msrc_cvrf(response.json(), cve_id)


@dataclass(slots=True)
class MsrcAdapter:
    """Record MSRC CVRF data for each spec CVE MSRC has data for."""

    source_id: str = _SOURCE_ID
    priority: LookupPriority = LookupPriority.OTHER

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Append an ``MsrcRecord`` per Microsoft-issued CVE (one budget call each)."""
        refs = support.cve_refs(ctx.spec)
        if not refs:
            return
        client = ctx.clients.msrc
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
                    reason=f"external API budget exhausted before {cve.cve_id} MSRC lookup",
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
                ctx.result.msrc_records.append(record)


__all__ = ["HttpxMsrcClient", "MsrcAdapter", "parse_msrc_cvrf"]
