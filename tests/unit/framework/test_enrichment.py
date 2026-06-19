"""Tests for the pre-Planner enrichment pass (``cyberlab_gen.framework.enrichment``).

Architectural source: ``pipeline.md §3.2.4``, ``implementation-plan.md §4.2``,
ADR 0020.

These exercise the four behaviors the brief's exit criteria name:

1. a CVE field gets NVD-enriched with both citations and ``source=external_api``;
2. a contradicting (cross-tier) CVSS produces a ``material_discrepancies`` entry;
3. a same-tier difference rewrites silently with the discrepancy recorded in
   provenance but *not* in ``material_discrepancies``;
4. budget exhaustion and rate-limiting both degrade gracefully (skips, no raise).

Plus: framework-only authorship (every rewrite is ``external_api``), MITRE local
validation (seed-listed → enriched, well-formed uncatalogued → unverified skip, never a
false discrepancy — ADR 0055/0058), and stub-source honesty.

A fake ``NvdClient`` returns recorded fixtures (no live NVD call), which is the
VCR-equivalent for a pure-Python client surface — the client *is* the seam the
brief's "recorded NVD/MITRE fixtures" intent targets.
"""

from cyberlab_gen.errors import ExternalApiRateLimitError
from cyberlab_gen.framework.enrichment import (
    EnrichmentConfig,
    NvdCveData,
    enrich,
)
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    CveReference,
    ExternalRefsBlock,
    ExtractionMetadataBlock,
    MitreTechniqueReference,
    PerStepReproducibility,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
    Severity,
)
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceFloat,
    ProvenanceString,
)
from cyberlab_gen.schemas.registries import (
    MitreTechniqueCatalog,
    MitreTechniqueEntry,
)

# --- fakes -----------------------------------------------------------------


class _FakeNvd:
    """A fake ``NvdClient`` returning canned ``NvdCveData`` keyed by CVE id."""

    def __init__(self, table: dict[str, NvdCveData | None]) -> None:
        self.table = table
        self.calls: list[str] = []

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        self.calls.append(cve_id)
        return self.table.get(cve_id)


class _RateLimitedNvd:
    """A fake ``NvdClient`` that always rate-limits."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        self.calls.append(cve_id)
        raise ExternalApiRateLimitError(f"rate-limited {cve_id}")


def _mitre_catalog() -> MitreTechniqueCatalog:
    return MitreTechniqueCatalog(
        entries=[
            MitreTechniqueEntry(
                name="T1078",  # type: ignore[arg-type]
                display_name="Valid Accounts",
                tactic="defense-evasion",
                description="d",
            ),
            MitreTechniqueEntry(
                name="T1552.005",  # type: ignore[arg-type]
                display_name="Cloud Instance Metadata API",
                tactic="credential-access",
                description="d",
            ),
        ]
    )


# --- small AttackSpec builders ---------------------------------------------

_HASH = "a" * 64


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])


def _source() -> SourceBlock:
    return SourceBlock(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        title="t",
        publisher={"name": "n", "domain": "d.com", "kind": "researcher_personal"},  # type: ignore[arg-type]
        fetched_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        content_hash=_HASH,
        fetch_method="http_get",
        word_count=10,
    )


def _metadata() -> ExtractionMetadataBlock:
    return ExtractionMetadataBlock(
        extractor_version="1.0.0",
        model="mock",
        completeness_score=0.5,
        citations_count=1,
    )


def _thesis() -> ThesisBlock:
    return ThesisBlock(
        types=["vulnerability_chain"],  # type: ignore[list-item]
        summary=_pstr("s"),
        attacker_objective=_pstr("o"),
        vulnerability_story=_pstr("v"),
        duration_as_described=_pstr("d"),
    )


def _step(mitre: list[str]) -> ChainStep:
    return ChainStep(
        id="step-1",  # type: ignore[arg-type]
        step_number=1,
        title="s",
        description=_pstr("d"),
        blog_excerpt="excerpt",
        techniques=ChainStepTechniques(mitre=mitre),  # type: ignore[arg-type]
        reproducibility=PerStepReproducibility(
            classification="full",  # type: ignore[arg-type]
            caveats=_pstr("none"),
            why=_pstr("scriptable"),
        ),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _spec(
    *,
    cves: list[CveReference] | None = None,
    mitre: list[str] | None = None,
    mitre_refs: list[MitreTechniqueReference] | None = None,
) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=_thesis(),
        chain=ChainBlock(chain_steps=[_step(mitre or [])]),
        external_references=ExternalRefsBlock(
            cves=cves or [],
            mitre_techniques=mitre_refs or [],
        ),
        extraction_metadata=_metadata(),
    )


def _cve(
    cve_id: str,
    *,
    cvss: float | None = None,
    severity: Severity | None = None,
) -> CveReference:
    return CveReference(
        cve_id=cve_id,  # type: ignore[arg-type]
        description=_pstr("a cve"),
        cvss_score=(
            ProvenanceFloat(value=cvss, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])
            if cvss is not None
            else None
        ),
        severity=(
            Provenance[Severity](
                value=severity, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()]
            )
            if severity is not None
            else None
        ),
    )


def _no_registry_config(
    *,
    nvd_client: _FakeNvd | _RateLimitedNvd | None = None,
    budget: int = 100,
) -> EnrichmentConfig:
    """An EnrichmentConfig that uses the bundled registries + an injected MITRE catalog.

    The bundled ``external_data_sources`` registry supplies the NVD entry (with
    its ``cvss_score``-is-material rule); the MITRE catalog is injected so tests
    don't depend on the bundled seed growing.
    """
    return EnrichmentConfig(
        mitre_catalog=_mitre_catalog(),
        nvd_client=nvd_client,
        budget=budget,
    )


# --- 1. CVE field gets NVD-enriched with both citations + external_api ------


def test_cve_field_enriched_with_external_api_and_both_citations() -> None:
    spec = _spec(cves=[_cve("CVE-2021-44228")])  # no blog cvss -> pure fill
    client = _FakeNvd(
        {
            "CVE-2021-44228": NvdCveData(
                cve_id="CVE-2021-44228", cvss_score=10.0, cvss_severity="CRITICAL"
            )
        }
    )
    result = enrich(spec, _no_registry_config(nvd_client=client))

    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.source_of_record == "nvd"
    assert cve.cvss_score is not None
    assert cve.cvss_score.source is ProvenanceSource.EXTERNAL_API
    assert cve.cvss_score.value == 10.0
    # both citations present: blog passage + external API response.
    kinds = {c.kind for c in cve.cvss_score.citations}
    assert CitationKind.BLOG_PASSAGE in kinds
    assert CitationKind.EXTERNAL_API_RESPONSE in kinds
    # severity also enriched onto the closed enum.
    assert cve.severity is not None
    assert cve.severity.value is Severity.CRITICAL
    assert cve.severity.source is ProvenanceSource.EXTERNAL_API
    assert not spec.material_discrepancies
    assert result.calls_made == 1
    assert client.calls == ["CVE-2021-44228"]


# --- 2. contradicting (cross-tier) CVSS -> material_discrepancies -----------


def test_cross_tier_cvss_contradiction_is_material() -> None:
    # blog says LOW (tier 1), NVD says CRITICAL (tier 4) -> cross-tier -> material.
    spec = _spec(cves=[_cve("CVE-2021-0001", cvss=3.0, severity=Severity.LOW)])
    client = _FakeNvd(
        {
            "CVE-2021-0001": NvdCveData(
                cve_id="CVE-2021-0001", cvss_score=9.8, cvss_severity="CRITICAL"
            )
        }
    )
    result = enrich(spec, _no_registry_config(nvd_client=client))

    assert len(spec.material_discrepancies) >= 1
    md = spec.material_discrepancies[0]
    assert md.source_of_record == "nvd"
    assert "CVE-2021-0001" in md.field_path
    # the field provenance carries the material classification + override.
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.severity is not None
    assert cve.severity.discrepancy_with_blog is True
    assert cve.severity.discrepancy_classification == "material"
    assert cve.severity.overridden_blog_value is Severity.LOW
    assert result.material_discrepancies  # mirrored in the result account


# --- 3. same-tier difference -> silent rewrite, recorded in provenance ------


def test_same_tier_cvss_difference_is_non_material_silent_rewrite() -> None:
    # blog HIGH (tier 3) numeric 7.0; NVD HIGH (tier 3) numeric 8.9. The
    # numeric cvss_score differs but the cvss_score materiality rule (registry)
    # is "material"; severity is same-tier -> non-material. We assert the
    # severity path is the silent one.
    spec = _spec(cves=[_cve("CVE-2021-0002", severity=Severity.HIGH)])
    client = _FakeNvd(
        {"CVE-2021-0002": NvdCveData(cve_id="CVE-2021-0002", cvss_severity="HIGH", cvss_score=8.9)}
    )
    enrich(spec, _no_registry_config(nvd_client=client))

    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    # severity rewritten to external_api but values agree (both HIGH) -> no
    # discrepancy at all; the enrichment is a clean fill.
    assert cve.severity is not None
    assert cve.severity.source is ProvenanceSource.EXTERNAL_API
    assert cve.severity.discrepancy_with_blog is False
    # No material discrepancy from a same-tier agreement.
    assert not spec.material_discrepancies


def test_numeric_cvss_contradiction_is_material_via_registry_rule() -> None:
    # The bundled NVD entry classifies the ``cvss_score`` field as *material*.
    # A differing numeric CVSS (blog 3.0, NVD 9.8) is therefore a material
    # discrepancy recorded both in provenance and in material_discrepancies.
    spec = _spec(cves=[_cve("CVE-2021-0009", cvss=3.0)])
    client = _FakeNvd(
        {
            "CVE-2021-0009": NvdCveData(
                cve_id="CVE-2021-0009", cvss_score=9.8, cvss_severity="CRITICAL"
            )
        }
    )
    enrich(spec, _no_registry_config(nvd_client=client))

    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None
    assert cve.cvss_score.discrepancy_with_blog is True
    assert cve.cvss_score.discrepancy_classification == "material"
    assert cve.cvss_score.overridden_blog_value == 3.0
    score_paths = [m.field_path for m in spec.material_discrepancies]
    assert any("cvss_score" in p for p in score_paths)


# --- 4a. budget exhaustion degrades gracefully -----------------------------


def test_budget_exhaustion_skips_remaining_cves() -> None:
    spec = _spec(cves=[_cve("CVE-2021-0003"), _cve("CVE-2021-0004")])
    client = _FakeNvd(
        {
            "CVE-2021-0003": NvdCveData(
                cve_id="CVE-2021-0003", cvss_score=5.0, cvss_severity="MEDIUM"
            ),
            "CVE-2021-0004": NvdCveData(
                cve_id="CVE-2021-0004", cvss_score=5.0, cvss_severity="MEDIUM"
            ),
        }
    )
    result = enrich(spec, _no_registry_config(nvd_client=client, budget=1))

    assert result.calls_made == 1
    assert client.calls == ["CVE-2021-0003"]  # only the first, budget then exhausted
    skipped_reasons = [s.reason for s in result.skipped]
    assert any("budget exhausted" in r for r in skipped_reasons)


# --- 4b. rate-limiting degrades gracefully ---------------------------------


def test_rate_limit_records_skip_and_continues() -> None:
    spec = _spec(cves=[_cve("CVE-2021-0005")])
    client = _RateLimitedNvd()
    result = enrich(spec, _no_registry_config(nvd_client=client, budget=10))

    # No raise; a skip with the exact mandated reason.
    assert any(s.reason == "external API rate-limited at enrichment time" for s in result.skipped)
    # The CVE field was left as the blog had it (not rewritten to external_api).
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.source_of_record is None
    assert not spec.material_discrepancies


# --- MITRE local validation ------------------------------------------------


def test_known_mitre_enriched_uncatalogued_is_unverified_not_material() -> None:
    # ADR 0055/0058 (item 1b): the bundled seed is not an authority, so a well-formed
    # uncatalogued technique id is recorded as UNVERIFIED (an honest skip), NOT as a false
    # "contradicting technique" material discrepancy. A catalogued id is still enriched.
    spec = _spec(mitre=["T1078", "T9999"])  # one in the seed, one not
    result = enrich(spec, _no_registry_config())

    assert "technique.T1078" in result.enriched_field_paths
    # The uncatalogued id is NOT a material discrepancy (the false positive we removed).
    assert not spec.material_discrepancies
    assert not result.material_discrepancies
    # It is recorded honestly as an unverified/skipped MITRE lookup instead.
    assert any(
        s.field_path == "technique.T9999" and s.source_id == "mitre_attack" for s in result.skipped
    )


def test_standalone_mitre_reference_is_validated() -> None:
    spec = _spec(
        mitre_refs=[
            MitreTechniqueReference(
                technique_id="T1552.005",  # type: ignore[arg-type]
                name=_pstr("Cloud Instance Metadata API"),
            )
        ]
    )
    result = enrich(spec, _no_registry_config())
    assert "technique.T1552.005" in result.enriched_field_paths


def test_mitre_lookup_does_not_consume_budget() -> None:
    spec = _spec(mitre=["T1078"])
    result = enrich(spec, _no_registry_config(budget=0))
    # budget 0, but MITRE is local -> still enriched, no external calls.
    assert result.calls_made == 0
    assert "technique.T1078" in result.enriched_field_paths


# --- framework-only authorship & stub honesty ------------------------------


def test_no_client_skips_cves_as_not_integrated() -> None:
    spec = _spec(cves=[_cve("CVE-2021-0006")])
    result = enrich(spec, _no_registry_config(nvd_client=None))
    assert result.calls_made == 0
    assert any("not integrated" in s.reason for s in result.skipped)
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.source_of_record is None  # untouched


def test_enrichment_with_no_external_refs_is_a_noop() -> None:
    spec = _spec()  # no cves, no mitre
    result = enrich(spec, _no_registry_config())
    assert result.calls_made == 0
    assert not result.material_discrepancies
    assert not spec.material_discrepancies


def test_every_enriched_field_is_external_api_authored() -> None:
    """Framework-only authorship: no enriched provenance is anything but external_api."""
    spec = _spec(cves=[_cve("CVE-2021-0007", cvss=2.0)])
    client = _FakeNvd(
        {"CVE-2021-0007": NvdCveData(cve_id="CVE-2021-0007", cvss_score=2.0, cvss_severity="LOW")}
    )
    enrich(spec, _no_registry_config(nvd_client=client))
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None
    assert cve.cvss_score.source is ProvenanceSource.EXTERNAL_API
    assert cve.severity is not None
    assert cve.severity.source is ProvenanceSource.EXTERNAL_API


# --- framework_enriched mark (ADR 0052 / 0061) -----------------------------


def test_enriched_fields_are_marked_framework_enriched_clean_fill() -> None:
    # The clean (non-discrepant) rewrite branch must stamp framework_enriched=True so the
    # grounding stack + jury EXEMPT the framework's own NVD call from search-before-claim.
    spec = _spec(cves=[_cve("CVE-2021-0010")])  # no blog cvss -> pure fill
    client = _FakeNvd(
        {"CVE-2021-0010": NvdCveData(cve_id="CVE-2021-0010", cvss_score=7.0, cvss_severity="HIGH")}
    )
    enrich(spec, _no_registry_config(nvd_client=client))
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None and cve.cvss_score.framework_enriched is True
    assert cve.severity is not None and cve.severity.framework_enriched is True


def test_enriched_fields_are_marked_framework_enriched_discrepant() -> None:
    # The discrepant rewrite branch must also stamp framework_enriched=True.
    spec = _spec(cves=[_cve("CVE-2021-0011", cvss=3.0, severity=Severity.LOW)])
    client = _FakeNvd(
        {
            "CVE-2021-0011": NvdCveData(
                cve_id="CVE-2021-0011", cvss_score=9.8, cvss_severity="CRITICAL"
            )
        }
    )
    enrich(spec, _no_registry_config(nvd_client=client))
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None and cve.cvss_score.framework_enriched is True
    assert cve.cvss_score.discrepancy_with_blog is True  # both marks coexist
    assert cve.severity is not None and cve.severity.framework_enriched is True


def test_untouched_field_keeps_framework_enriched_false() -> None:
    # A CVE field NVD does not enrich (no client) keeps framework_enriched=False.
    spec = _spec(cves=[_cve("CVE-2021-0012", cvss=5.0)])
    enrich(spec, _no_registry_config(nvd_client=None))
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None and cve.cvss_score.framework_enriched is False


def test_re_enrichment_is_idempotent_no_double_discrepancy() -> None:
    # C1 re-runs enrichment after a jury-revise patch. Re-running on an already-enriched spec
    # must NOT double-append material_discrepancies, and the field stays framework_enriched.
    spec = _spec(cves=[_cve("CVE-2021-0013", cvss=3.0, severity=Severity.LOW)])
    client = _FakeNvd(
        {
            "CVE-2021-0013": NvdCveData(
                cve_id="CVE-2021-0013", cvss_score=9.8, cvss_severity="CRITICAL"
            )
        }
    )
    enrich(spec, _no_registry_config(nvd_client=client))
    first_count = len(spec.material_discrepancies)
    assert first_count >= 1
    enrich(spec, _no_registry_config(nvd_client=client))  # re-run on the already-enriched spec
    assert len(spec.material_discrepancies) == first_count  # no double-append
    cve = spec.external_references.cves[0]  # type: ignore[union-attr]
    assert cve.cvss_score is not None and cve.cvss_score.framework_enriched is True
    # the original field-level discrepancy marking survives the re-run (not clobbered)
    assert cve.cvss_score.discrepancy_with_blog is True
