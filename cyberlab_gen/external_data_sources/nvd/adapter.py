"""The NVD adapter + its ``httpx``-backed client.

Encapsulates the CVE-metadata enrichment that used to live inline in
``framework.enrichment`` (``_enrich_cves`` / ``_apply_cve_enrichment`` /
``_rewrite_*``), now behind the ``SourceAdapter`` seam (ADR 0101). Behaviour is
preserved: per CVE it rewrites ``cvss_score`` / ``severity`` provenance to
``external_api`` with both citations, records a discrepancy when the blog claimed
a different value, and classifies materiality by CVSS tier. It additionally
records the per-CVE ``CveResolution`` the grounding ship-gate consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberlab_gen.errors import ExternalApiRateLimitError, ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import materiality, support
from cyberlab_gen.external_data_sources.nvd.parsing import parse_nvd_response
from cyberlab_gen.external_data_sources.types import CveResolution, LookupPriority
from cyberlab_gen.schemas.enums import ProvenanceSource, Severity
from cyberlab_gen.schemas.provenance import Provenance, ProvenanceFloat

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.external_data_sources.types import EnrichmentResult, NvdCveData
    from cyberlab_gen.schemas.attack_spec import AttackSpec, CveReference
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry


@dataclass(slots=True)
class HttpxNvdClient:
    """An ``httpx``-backed ``NvdClient`` (live, VCR-recordable).

    Injecting an ``httpx.Client`` (or a ``MockTransport``-backed one) lets tests
    replay recorded cassettes. A 429 raises ``ExternalApiRateLimitError`` and any
    other transport/HTTP failure raises ``ExternalApiUnavailableError`` so the
    enrichment pass degrades gracefully (``pipeline.md §3.2.4``, ADR 0042); a 404
    means NVD has no record (``None``).
    """

    client: object  # an httpx.Client; typed loosely to avoid a hard import here
    base_url: str

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        """Look up ``cve_id`` against NVD v2 and parse the response."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxNvdClient.client must be an httpx.Client")
        try:
            response = self.client.get(self.base_url, params={"cveId": cve_id})
        except httpx.HTTPError as exc:
            raise ExternalApiUnavailableError(f"NVD unreachable looking up {cve_id}") from exc
        if response.status_code == 429:
            raise ExternalApiRateLimitError(f"NVD rate-limited looking up {cve_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ExternalApiUnavailableError(
                f"NVD returned {response.status_code} looking up {cve_id}"
            )
        return parse_nvd_response(response.json())


@dataclass(slots=True)
class NvdAdapter:
    """Enrich CVE references via NVD (the first adapter behind the seam)."""

    source_id: str = "nvd"
    priority: LookupPriority = LookupPriority.CVE

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Enrich every CVE reference via NVD, rewriting provenance + recording discrepancies."""
        refs = support.cve_refs(ctx.spec)
        if not refs:
            return

        client = ctx.clients.nvd
        if client is None:
            for cve in refs:
                support.record_skip(
                    ctx.result,
                    field_path=f"external_references.cves[{cve.cve_id}]",
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
                    reason=f"external API budget exhausted before {cve.cve_id} lookup",
                )
                continue

            try:
                data = client.lookup_cve(cve.cve_id)
            except ExternalApiRateLimitError:
                ctx.result.cve_resolution[cve.cve_id] = CveResolution.UNAVAILABLE
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=support.RATE_LIMITED_REASON,
                )
                continue
            except ExternalApiUnavailableError:
                ctx.result.cve_resolution[cve.cve_id] = CveResolution.UNAVAILABLE
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=support.UNAVAILABLE_REASON.format(source_id=entry.id),
                )
                continue

            ctx.budget[0] -= 1
            ctx.result.calls_made += 1

            if data is None:
                ctx.result.cve_resolution[cve.cve_id] = CveResolution.ABSENT
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=f"NVD has no record for {cve.cve_id}",
                )
                continue

            ctx.result.cve_resolution[cve.cve_id] = CveResolution.CONFIRMED
            self._apply(ctx.spec, ctx.result, cve, entry, data)

    def _apply(
        self,
        spec: AttackSpec,
        result: EnrichmentResult,
        cve: CveReference,
        entry: ExternalDataSourceEntry,
        data: NvdCveData,
    ) -> None:
        """Rewrite a ``CveReference``'s cvss/severity provenance from NVD data."""
        cve.source_of_record = entry.id  # type: ignore[assignment]
        if data.cvss_score is not None:
            self._rewrite_cvss_score(spec, result, cve, entry, data.cvss_score)
        if data.cvss_severity is not None:
            self._rewrite_severity(spec, result, cve, entry, data.cvss_severity)

    def _rewrite_cvss_score(
        self,
        spec: AttackSpec,
        result: EnrichmentResult,
        cve: CveReference,
        entry: ExternalDataSourceEntry,
        api_score: float,
    ) -> None:
        """Set ``cve.cvss_score`` to the NVD value with external_api provenance.

        If the blog stated a numeric CVSS that differs, record a discrepancy and
        classify it via the ``cvss_score`` materiality rule.
        """
        # Idempotency (ADR 0052 / 0061): C1 re-runs enrichment after a jury-revise patch. A field
        # already framework_enriched by a prior pass is a no-op — re-stamping would lose the
        # original field-level discrepancy marking, and re-reading it (now external_api, no
        # blog_explicit value) would silently drop the discrepancy. Skip it.
        if cve.cvss_score is not None and cve.cvss_score.framework_enriched:
            return
        path = f"external_references.cves[{cve.cve_id}].cvss_score"
        blog = cve.cvss_score
        blog_value: float | None = None
        if blog is not None and blog.source is ProvenanceSource.BLOG_EXPLICIT:
            blog_value = blog.value
        discrepant = blog_value is not None and blog_value != api_score

        citations = [support.blog_citation(cve.cve_id), support.api_citation(entry.id, cve.cve_id)]
        if discrepant:
            assert blog_value is not None
            classification = materiality.classify(entry, "cvss_score")
            cve.cvss_score = ProvenanceFloat(
                value=api_score,
                source=ProvenanceSource.EXTERNAL_API,
                citations=citations,
                framework_enriched=True,
                discrepancy_with_blog=True,
                overridden_blog_value=blog_value,
                discrepancy_classification=classification,  # type: ignore[arg-type]
            )
            support.record_discrepancy(
                spec,
                result,
                path=path,
                blog_value=str(blog_value),
                authoritative_value=str(api_score),
                source_id=entry.id,
                classification=classification,
                summary=f"NVD CVSS score {api_score} differs from blog-stated {blog_value}",
            )
        else:
            cve.cvss_score = ProvenanceFloat(
                value=api_score,
                source=ProvenanceSource.EXTERNAL_API,
                citations=citations,
                framework_enriched=True,
            )
            result.enriched_field_paths.append(path)

    def _rewrite_severity(
        self,
        spec: AttackSpec,
        result: EnrichmentResult,
        cve: CveReference,
        entry: ExternalDataSourceEntry,
        api_severity: str,
    ) -> None:
        """Set ``cve.severity`` to the NVD qualitative severity with external_api provenance.

        Materiality on severity is tier-based: a same-tier difference is non-material,
        a cross-tier difference is material (``pipeline.md §3.2.4``). The NVD severity
        is mapped onto the closed ``Severity`` enum; an unmappable value is skipped.
        """
        api_sev = materiality.severity_from_cvss(api_severity)
        if api_sev is None:
            return

        # Idempotency (ADR 0052 / 0061): a no-op on an already-enriched field.
        if cve.severity is not None and cve.severity.framework_enriched:
            return
        path = f"external_references.cves[{cve.cve_id}].severity"
        blog = cve.severity
        blog_value: Severity | None = None
        if blog is not None and blog.source is ProvenanceSource.BLOG_EXPLICIT:
            blog_value = blog.value
        discrepant = blog_value is not None and blog_value is not api_sev

        citations = [support.blog_citation(cve.cve_id), support.api_citation(entry.id, cve.cve_id)]
        if discrepant:
            assert blog_value is not None
            classification = materiality.severity_materiality(blog_value, api_sev)
            cve.severity = Provenance[Severity](
                value=api_sev,
                source=ProvenanceSource.EXTERNAL_API,
                citations=citations,
                framework_enriched=True,
                discrepancy_with_blog=True,
                overridden_blog_value=blog_value,
                discrepancy_classification=classification,  # type: ignore[arg-type]
            )
            support.record_discrepancy(
                spec,
                result,
                path=path,
                blog_value=str(blog_value),
                authoritative_value=str(api_sev),
                source_id=entry.id,
                classification=classification,
                summary=f"NVD severity {api_sev} differs from blog-stated {blog_value}",
            )
        else:
            cve.severity = Provenance[Severity](
                value=api_sev,
                source=ProvenanceSource.EXTERNAL_API,
                citations=citations,
                framework_enriched=True,
            )
            result.enriched_field_paths.append(path)


__all__ = ["HttpxNvdClient", "NvdAdapter"]
