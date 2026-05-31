"""Pre-Planner enrichment: the deterministic framework pass (``pipeline.md §3.2.4``).

Framework code — **never an agent** (CLAUDE.md hard rules; ``schema.md §4.9``
"framework-only authorship"). Walks the ``enrichment_triggers`` declared by the
``external_data_sources`` registry entries and, for Phase 1, enriches:

- **CVE references** (``AttackSpec.external_references.cves[*]``) against NVD
  (live, via an injectable client so tests replay recorded fixtures).
- **MITRE technique references** (``...chain.chain_steps[*].techniques.mitre[*]``
  and ``external_references.mitre_techniques[*]``) against the bundled MITRE
  ATT&CK technique catalog (read locally, ``registry-details.md §5.1`` — no
  live call, no rate-limit).

The framework sets each enriched field's provenance to ``source=external_api``
with citations to *both* the blog passage and the API/catalog response. When the
external value contradicts a ``blog_explicit`` finding, the rewrite preserves
both citations and sets ``discrepancy_with_blog=True`` (always recorded for
audit, ``schema.md §4.9``). The discrepancy is then classified per the source
entry's ``discrepancy_materiality_rules``:

- **non-material** (same-tier CVSS, same CWE category, equivalent technique) →
  silent rewrite, recorded only in the field's ``Provenance``
  (``discrepancy_classification="non_material"``);
- **material** (cross-tier CVSS, different vector/CWE, contradicting technique) →
  the rewrite *also* appends a ``MaterialDiscrepancy`` to the AttackSpec's
  top-level ``material_discrepancies`` list. Phase 1 surfaces these in the run
  report only; the third interactive review surface lands in Phase 4
  (``implementation-plan.md §4.2``).

Budget (``pipeline.md §3.2.4``): a per-run cap (default 100) on framework-issued
external (non-local) calls, spent in priority order CVEs > MITRE > GitHub >
bulletins > other. Lookups skipped because the budget is exhausted, a source is
rate-limited, or a source is a registered-but-not-integrated stub are recorded
as ``SkippedLookup`` records carrying the ``unknown_from_blog`` reason that
names the gap honestly. MITRE catalog lookups are local and do not consume the
external-call budget.

Authorship discipline (``architecture.md §1.5/§1.6``): this is mechanical,
deterministic framework code. No LLM decides what to enrich, whether a
discrepancy is material, or whether to stop — those are registry-driven rules.
The materiality classification (``architecture.md §1.6``: mechanical safety
checks are never LLM-based) is pure rule lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import Field

from cyberlab_gen.errors import ExternalApiRateLimitError
from cyberlab_gen.registries.loader import (
    load_merged_registries,
    load_mitre_techniques,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec, MaterialDiscrepancy
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ProvenanceSource,
    Severity,
)
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceFloat,
)

if TYPE_CHECKING:
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import CveReference
    from cyberlab_gen.schemas.registries import (
        DiscrepancyMaterialityRule,
        ExternalDataSourceEntry,
        MitreTechniqueCatalog,
    )

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = 100
_NVD_SOURCE_ID = "nvd"
_MITRE_SOURCE_ID = "mitre_attack_techniques"

#: Reason recorded for a source that is registered in the registry but not
#: wired to a live/local lookup in Phase 1 (MSRC/OSV/KEV/EPSS/cloud-bulletins).
_NOT_INTEGRATED_REASON = "source {source_id} not integrated in Phase 1"
#: Reason recorded when an external source rate-limits a framework lookup
#: (``pipeline.md §3.2.4``). The exact wording the brief mandates.
_RATE_LIMITED_REASON = "external API rate-limited at enrichment time"

#: CVSS qualitative-severity tiers, ordered low→high. Same-tier CVSS differences
#: are non-material; cross-tier differences are material (``pipeline.md §3.2.4``).
_CVSS_TIERS: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class LookupPriority(IntEnum):
    """Budget-spend priority order (``pipeline.md §3.2.4``).

    Lower value = spent first: CVEs > MITRE > GitHub > bulletins > other.
    (MITRE is local in Phase 1 and does not consume the call budget, but the
    ordering is preserved so the budget walker stays correct when MITRE becomes
    a live source.)
    """

    CVE = 0
    MITRE = 1
    GITHUB = 2
    BULLETIN = 3
    OTHER = 4


# --- NVD response (the subset enrichment consumes) -------------------------


class NvdCveData(InternalModel):
    """The CVE metadata enrichment reads from an NVD response.

    Internal (``InternalModel``) — it never crosses a pipeline-stage boundary; it
    is parsed from the NVD JSON and consumed in-process. ``extra="ignore"`` lets
    the parser pick the few fields used out of NVD's large payload.
    """

    cve_id: str
    cvss_score: float | None = None
    cvss_severity: str | None = None
    cwe_ids: list[str] = Field(default_factory=list[str])
    description: str | None = None


class NvdClient(Protocol):
    """The narrow client surface enrichment needs from NVD.

    Injecting this (rather than constructing an ``httpx`` client inline) keeps
    enrichment testable: tests pass a fake that returns recorded fixtures or
    raises ``ExternalApiRateLimitError`` to exercise the degrade path.
    """

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        """Return parsed CVE data, or ``None`` when NVD has no record.

        Raises ``ExternalApiRateLimitError`` when rate-limited; the caller
        records the skip and continues.
        """
        ...


def _parse_nvd_response(payload: object) -> NvdCveData | None:
    """Parse the subset of an NVD v2 CVE response enrichment uses.

    NVD v2 shape: ``{"vulnerabilities": [{"cve": {"id", "descriptions",
    "weaknesses", "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore",
    "baseSeverity"}}]}}}]}``. Returns ``None`` when no vulnerability is present.
    Tolerant of missing fields (the metric set varies across CVEs).
    """
    if not isinstance(payload, dict):
        return None
    vulns = payload.get("vulnerabilities")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(vulns, list) or not vulns:
        return None
    first = vulns[0]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(first, dict):
        return None
    cve = first.get("cve")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(cve, dict):
        return None

    cve_id = cve.get("id")  # pyright: ignore[reportUnknownMemberType]
    score, severity = _extract_cvss(cve.get("metrics"))  # pyright: ignore[reportUnknownMemberType]
    return NvdCveData(
        cve_id=str(cve_id) if isinstance(cve_id, str) else "",
        cvss_score=score,
        cvss_severity=severity,
        cwe_ids=_extract_cwes(cve.get("weaknesses")),  # pyright: ignore[reportUnknownMemberType]
        description=_extract_description(cve.get("descriptions")),  # pyright: ignore[reportUnknownMemberType]
    )


def _extract_cvss(metrics: object) -> tuple[float | None, str | None]:
    """Pull ``(baseScore, baseSeverity)`` from an NVD ``metrics`` block."""
    if not isinstance(metrics, dict):
        return None, None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        block = metrics.get(key)  # pyright: ignore[reportUnknownMemberType]
        if isinstance(block, list) and block:
            entry = block[0]  # pyright: ignore[reportUnknownVariableType]
            if isinstance(entry, dict):
                data = entry.get("cvssData")  # pyright: ignore[reportUnknownMemberType]
                if isinstance(data, dict):
                    raw_score = data.get("baseScore")  # pyright: ignore[reportUnknownMemberType]
                    score = float(raw_score) if isinstance(raw_score, (int, float)) else None
                    raw_sev = data.get("baseSeverity")  # pyright: ignore[reportUnknownMemberType]
                    severity = str(raw_sev) if isinstance(raw_sev, str) else None
                    return score, severity
    return None, None


def _extract_cwes(weaknesses: object) -> list[str]:
    """Pull CWE ids from an NVD ``weaknesses`` block."""
    out: list[str] = []
    if not isinstance(weaknesses, list):
        return out
    for weakness in weaknesses:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(weakness, dict):
            descriptions = weakness.get("description")  # pyright: ignore[reportUnknownMemberType]
            if isinstance(descriptions, list):
                for desc in descriptions:  # pyright: ignore[reportUnknownVariableType]
                    if isinstance(desc, dict):
                        value = desc.get("value")  # pyright: ignore[reportUnknownMemberType]
                        if isinstance(value, str) and value.startswith("CWE-"):
                            out.append(value)
    return out


def _extract_description(descriptions: object) -> str | None:
    """Pull the English description from an NVD ``descriptions`` block."""
    if not isinstance(descriptions, list):
        return None
    for desc in descriptions:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(desc, dict) and desc.get("lang") == "en":  # pyright: ignore[reportUnknownMemberType]
            value = desc.get("value")  # pyright: ignore[reportUnknownMemberType]
            if isinstance(value, str):
                return value
    return None


@dataclass(slots=True)
class HttpxNvdClient:
    """An ``httpx``-backed ``NvdClient`` (live, VCR-recordable).

    Injecting an ``httpx.Client`` (or a ``MockTransport``-backed one) lets tests
    replay recorded cassettes. A 429 raises ``ExternalApiRateLimitError`` so the
    enrichment pass degrades gracefully (``pipeline.md §3.2.4``); a 404 means NVD
    has no record (``None``); other non-2xx raise.
    """

    client: object  # an httpx.Client; typed loosely to avoid a hard import here
    base_url: str

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        """Look up ``cve_id`` against NVD v2 and parse the response."""
        import httpx

        if not isinstance(self.client, httpx.Client):  # pragma: no cover - guard
            raise TypeError("HttpxNvdClient.client must be an httpx.Client")
        response = self.client.get(self.base_url, params={"cveId": cve_id})
        if response.status_code == 429:
            raise ExternalApiRateLimitError(f"NVD rate-limited looking up {cve_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _parse_nvd_response(response.json())


# --- Enrichment record (typed; the framework's account of what it did) -----


class SkippedLookup(InternalModel):
    """One lookup the framework chose not to (or could not) perform.

    Carries the ``unknown_from_blog``-style reason naming why (budget exhausted,
    rate limited, source not integrated, or NVD has no record). Surfaced in the
    run report so the gap is honest (``implementation-plan.md §4.2``).
    """

    field_path: str
    source_id: str
    reason: str


class EnrichmentResult(InternalModel):
    """The framework's account of an enrichment pass.

    The pass mutates the AttackSpec in place — it rewrites the target field's
    ``Provenance`` to ``external_api`` and appends to ``material_discrepancies``
    — and returns this account for the run report: which field paths it enriched,
    the material/non-material discrepancies it found, and the lookups it skipped.
    ``calls_made`` counts external (budget-consuming) calls only.
    """

    enriched_field_paths: list[str] = Field(default_factory=list[str])
    material_discrepancies: list[MaterialDiscrepancy] = Field(
        default_factory=list[MaterialDiscrepancy]
    )
    non_material_field_paths: list[str] = Field(default_factory=list[str])
    skipped: list[SkippedLookup] = Field(default_factory=list[SkippedLookup])
    calls_made: int = 0


# --- Config ----------------------------------------------------------------


@dataclass(slots=True)
class EnrichmentConfig:
    """Tunable knobs for the enrichment pass (``pipeline.md §3.2.4``)."""

    budget: int = _DEFAULT_BUDGET
    """Per-run cap on framework-issued external (non-local) calls."""

    nvd_client: NvdClient | None = None
    """Injected NVD client. ``None`` disables live CVE enrichment — every CVE
    lookup is then skipped with a not-integrated reason (used by hermetic unit
    tests that do not supply a client)."""

    mitre_catalog: MitreTechniqueCatalog | None = None
    """Injected MITRE catalog; defaults to the bundled one when ``None``."""

    registries: MergedRegistries | None = None
    """Injected merged registries (for the ``external_data_sources`` entries +
    their ``discrepancy_materiality_rules``); defaults to the bundled+overlay
    merge when ``None``."""

    integrated_sources: frozenset[str] = field(
        default_factory=lambda: frozenset({_NVD_SOURCE_ID, _MITRE_SOURCE_ID})
    )
    """Source ids wired to live/local lookups in Phase 1. Everything else in the
    registry is a registered stub and gets an honest skip reason."""


# --- Citations -------------------------------------------------------------


def _blog_citation(locator: str) -> CitationBlock:
    """A citation back to where the blog mentioned the id.

    The blog-passage locator is the id the framework found in the AttackSpec; the
    richer per-step excerpt lives on the chain step. Both citations are preserved
    on every rewrite (``schema.md §4.9``).
    """
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference=locator)


def _api_citation(source_id: str, locator: str) -> CitationBlock:
    """A citation back to the external API/catalog response."""
    return CitationBlock(
        kind=CitationKind.EXTERNAL_API_RESPONSE,
        reference=f"{source_id}:{locator}",
    )


# --- Materiality -----------------------------------------------------------


def _materiality_rule(
    entry: ExternalDataSourceEntry, field_path: str
) -> DiscrepancyMaterialityRule | None:
    """Return the materiality rule whose ``field_path`` matches, if any."""
    for rule in entry.discrepancy_materiality_rules:
        if rule.field_path == field_path:
            return rule
    return None


def _classify(entry: ExternalDataSourceEntry, rule_field: str) -> str:
    """Classify a discrepancy on ``rule_field`` per the entry's rules.

    Returns ``"material"`` or ``"non_material"``. Default when no rule names the
    field: **material** — the conservative reading (``pipeline.md §3.2.4``: "the
    framework never silently resolves a disagreement that would change the lab's
    character"). An unclassified field is treated as character-changing until a
    rule says otherwise.
    """
    rule = _materiality_rule(entry, rule_field)
    if rule is None:
        return "material"
    return rule.classification


def _cvss_tier(severity: str) -> int | None:
    """Map a qualitative CVSS severity string to its tier ordinal, if known."""
    return _CVSS_TIERS.get(severity.strip().lower())


def _severity_from_cvss(severity: str) -> Severity | None:
    """Map an NVD qualitative severity to the closed ``Severity`` enum, if it maps."""
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
    }
    return mapping.get(severity.strip().lower())


# --- CVE enrichment --------------------------------------------------------


def _enrich_cves(
    spec: AttackSpec,
    entry: ExternalDataSourceEntry,
    config: EnrichmentConfig,
    result: EnrichmentResult,
    budget_remaining: list[int],
) -> None:
    """Enrich every CVE reference via NVD, rewriting provenance + recording discrepancies.

    Operates on ``AttackSpec.external_references.cves`` (the NVD entry's
    ``external_references.cve_references[*]`` enrichment trigger). ``budget_remaining``
    is a single-element list used as a mutable counter shared across phases
    (CVEs spend first per priority order).
    """
    refs = spec.external_references
    if refs is None or not refs.cves:
        return

    if _NVD_SOURCE_ID not in config.integrated_sources or config.nvd_client is None:
        for cve in refs.cves:
            result.skipped.append(
                SkippedLookup(
                    field_path=f"external_references.cves[{cve.cve_id}]",
                    source_id=entry.id,
                    reason=_NOT_INTEGRATED_REASON.format(source_id=entry.id),
                )
            )
        return

    for cve in refs.cves:
        path = f"external_references.cves[{cve.cve_id}]"
        if budget_remaining[0] <= 0:
            result.skipped.append(
                SkippedLookup(
                    field_path=path,
                    source_id=entry.id,
                    reason=f"external API budget exhausted before {cve.cve_id} lookup",
                )
            )
            continue

        try:
            data = config.nvd_client.lookup_cve(cve.cve_id)
        except ExternalApiRateLimitError:
            logger.warning("NVD rate-limited at enrichment for %s", cve.cve_id)
            result.skipped.append(
                SkippedLookup(field_path=path, source_id=entry.id, reason=_RATE_LIMITED_REASON)
            )
            continue

        budget_remaining[0] -= 1
        result.calls_made += 1

        if data is None:
            result.skipped.append(
                SkippedLookup(
                    field_path=path,
                    source_id=entry.id,
                    reason=f"NVD has no record for {cve.cve_id}",
                )
            )
            continue

        _apply_cve_enrichment(spec, cve, entry, data, result)


def _apply_cve_enrichment(
    spec: AttackSpec,
    cve: CveReference,
    entry: ExternalDataSourceEntry,
    data: NvdCveData,
    result: EnrichmentResult,
) -> None:
    """Rewrite a ``CveReference``'s cvss/severity provenance from NVD data."""
    cve.source_of_record = entry.id

    if data.cvss_score is not None:
        _rewrite_cvss_score(spec, cve, entry, data.cvss_score, result)
    if data.cvss_severity is not None:
        _rewrite_severity(spec, cve, entry, data.cvss_severity, result)


def _rewrite_cvss_score(
    spec: AttackSpec,
    cve: CveReference,
    entry: ExternalDataSourceEntry,
    api_score: float,
    result: EnrichmentResult,
) -> None:
    """Set ``cve.cvss_score`` to the NVD value with external_api provenance.

    If the blog stated a numeric CVSS that differs, record a discrepancy and
    classify it via the ``cvss_score`` materiality rule.
    """
    path = f"external_references.cves[{cve.cve_id}].cvss_score"
    blog = cve.cvss_score
    blog_value: float | None = None
    if blog is not None and blog.source is ProvenanceSource.BLOG_EXPLICIT:
        blog_value = blog.value
    discrepant = blog_value is not None and blog_value != api_score

    citations = [_blog_citation(cve.cve_id), _api_citation(entry.id, cve.cve_id)]
    if discrepant:
        assert blog_value is not None
        classification = _classify(entry, "cvss_score")
        cve.cvss_score = ProvenanceFloat(
            value=api_score,
            source=ProvenanceSource.EXTERNAL_API,
            citations=citations,
            discrepancy_with_blog=True,
            overridden_blog_value=blog_value,
            discrepancy_classification=classification,  # type: ignore[arg-type]
        )
        _record_discrepancy(
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
        )
        result.enriched_field_paths.append(path)


def _rewrite_severity(
    spec: AttackSpec,
    cve: CveReference,
    entry: ExternalDataSourceEntry,
    api_severity: str,
    result: EnrichmentResult,
) -> None:
    """Set ``cve.severity`` to the NVD qualitative severity with external_api provenance.

    Materiality on severity is tier-based: a same-tier difference is non-material,
    a cross-tier difference is material (``pipeline.md §3.2.4``). The NVD severity
    is mapped onto the closed ``Severity`` enum; an unmappable value is skipped.
    """
    api_sev = _severity_from_cvss(api_severity)
    if api_sev is None:
        return

    path = f"external_references.cves[{cve.cve_id}].severity"
    blog = cve.severity
    blog_value: Severity | None = None
    if blog is not None and blog.source is ProvenanceSource.BLOG_EXPLICIT:
        blog_value = blog.value
    discrepant = blog_value is not None and blog_value is not api_sev

    citations = [_blog_citation(cve.cve_id), _api_citation(entry.id, cve.cve_id)]
    if discrepant:
        assert blog_value is not None
        classification = _severity_materiality(blog_value, api_sev)
        cve.severity = Provenance[Severity](
            value=api_sev,
            source=ProvenanceSource.EXTERNAL_API,
            citations=citations,
            discrepancy_with_blog=True,
            overridden_blog_value=blog_value,
            discrepancy_classification=classification,  # type: ignore[arg-type]
        )
        _record_discrepancy(
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
        )
        result.enriched_field_paths.append(path)


def _severity_materiality(blog: Severity, api: Severity) -> str:
    """Classify a blog-vs-API severity difference by CVSS tier.

    Same tier → non-material; cross-tier → material. ``Severity`` members map
    directly onto CVSS qualitative tiers.
    """
    blog_tier = _cvss_tier(str(blog))
    api_tier = _cvss_tier(str(api))
    if blog_tier is None or api_tier is None:
        return "material"
    return "non_material" if blog_tier == api_tier else "material"


def _record_discrepancy(
    spec: AttackSpec,
    result: EnrichmentResult,
    *,
    path: str,
    blog_value: str,
    authoritative_value: str,
    source_id: str,
    classification: str,
    summary: str,
) -> None:
    """Record a (material or non-material) discrepancy.

    Always recorded for audit. Material ones additionally append a
    ``MaterialDiscrepancy`` to the spec's top-level index list.
    """
    if classification == "material":
        discrepancy = MaterialDiscrepancy(
            field_path=path,
            summary=summary,
            blog_value=blog_value,
            authoritative_value=authoritative_value,
            source_of_record=source_id,
        )
        result.material_discrepancies.append(discrepancy)
        spec.material_discrepancies.append(discrepancy)
    else:
        result.non_material_field_paths.append(path)


# --- MITRE enrichment (local catalog) --------------------------------------


def _collect_technique_refs(spec: AttackSpec) -> list[str]:
    """Gather every MITRE technique id referenced in the AttackSpec (de-duped).

    Covers the chain-step ``techniques.mitre[*]`` trigger and the standalone
    ``external_references.mitre_techniques[*]`` references.
    """
    seen: dict[str, None] = {}
    if spec.chain is not None:
        for step in spec.chain.chain_steps:
            for tech in step.techniques.mitre:
                seen.setdefault(tech, None)
    if spec.external_references is not None:
        for ref in spec.external_references.mitre_techniques:
            seen.setdefault(ref.technique_id, None)
    return list(seen.keys())


def _enrich_techniques(
    spec: AttackSpec,
    config: EnrichmentConfig,
    result: EnrichmentResult,
) -> None:
    """Validate/enrich MITRE technique ids against the bundled local catalog.

    Local lookup — no live call, no budget spend, no rate-limit. A technique id
    present in the catalog is recorded as enriched; a technique id *absent* from
    the catalog is a contradicting-technique discrepancy → material
    (``pipeline.md §3.2.4``).
    """
    tech_refs = _collect_technique_refs(spec)
    if not tech_refs:
        return

    catalog = config.mitre_catalog if config.mitre_catalog is not None else load_mitre_techniques()
    known: dict[str, str] = {t.name: t.display_name for t in catalog.entries}

    for tech in tech_refs:
        path = f"technique.{tech}"
        if tech in known:
            result.enriched_field_paths.append(path)
        else:
            discrepancy = MaterialDiscrepancy(
                field_path=path,
                summary=f"technique {tech} not present in the bundled MITRE ATT&CK catalog",
                blog_value=tech,
                authoritative_value="(not found in MITRE ATT&CK catalog)",
                source_of_record=_MITRE_SOURCE_ID,
            )
            result.material_discrepancies.append(discrepancy)
            spec.material_discrepancies.append(discrepancy)


# --- Stub sources ----------------------------------------------------------


def _record_stub_skips(
    entry: ExternalDataSourceEntry,
    config: EnrichmentConfig,
    result: EnrichmentResult,
) -> None:
    """Record honest skips for a registered-but-not-integrated source.

    For a source that declares ``enrichment_triggers`` but is not in
    ``integrated_sources`` (MSRC/OSV/KEV/EPSS/cloud-bulletins), the absence is
    surfaced once per declared trigger.
    """
    if entry.id in config.integrated_sources:
        return
    for trigger in entry.enrichment_triggers:
        result.skipped.append(
            SkippedLookup(
                field_path=str(trigger.field),
                source_id=entry.id,
                reason=_NOT_INTEGRATED_REASON.format(source_id=entry.id),
            )
        )


# --- Public entrypoint -----------------------------------------------------


def enrich(spec: AttackSpec, config: EnrichmentConfig | None = None) -> EnrichmentResult:
    """Run the pre-Planner enrichment pass over ``spec`` (``pipeline.md §3.2.4``).

    Mutates ``spec`` in place: rewrites enriched ``CveReference`` provenance to
    ``external_api`` and appends material discrepancies to
    ``spec.material_discrepancies``. Returns the full ``EnrichmentResult`` (the
    account the run report consumes). Framework code only — never an agent.
    """
    cfg = config if config is not None else EnrichmentConfig()
    result = EnrichmentResult()
    budget_remaining = [cfg.budget]

    registries = cfg.registries if cfg.registries is not None else load_merged_registries()
    sources = registries.external_data_sources

    # CVEs first (priority order). NVD is the only integrated external source.
    nvd_entry = sources.get(_NVD_SOURCE_ID)
    if nvd_entry is not None:
        _enrich_cves(spec, nvd_entry, cfg, result, budget_remaining)

    # MITRE techniques (local catalog; no budget spend).
    _enrich_techniques(spec, cfg, result)

    # Registered-but-not-integrated sources: honest skips.
    for entry in sources.values():
        if entry.id == _NVD_SOURCE_ID:
            continue
        _record_stub_skips(entry, cfg, result)

    logger.info(
        "enrichment: %d calls, %d enriched, %d material, %d non-material, %d skipped",
        result.calls_made,
        len(result.enriched_field_paths),
        len(result.material_discrepancies),
        len(result.non_material_field_paths),
        len(result.skipped),
    )
    return result


__all__ = [
    "EnrichmentConfig",
    "EnrichmentResult",
    "HttpxNvdClient",
    "LookupPriority",
    "NvdClient",
    "NvdCveData",
    "SkippedLookup",
    "enrich",
]
