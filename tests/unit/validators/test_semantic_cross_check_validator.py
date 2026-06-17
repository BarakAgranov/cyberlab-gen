"""Tests for the semantic cross-check validator (``validation.md §6.5``, Phase 2 Task 5).

The second mechanical validation layer over the ``LabManifest``. In Phase 2 the
**cross-block-within-manifest** checks are live (facet ``implies`` /
``incompatible_with``, ``produces_world_state`` ``identifier_source`` resolution); the
**code-vs-manifest** checks need generated code and are inert until Phase 3. The layer
**flags findings; it never mutates the manifest** (``§6.5``, ``architecture.md §1.6``).

Each test fails meaningfully if the behaviour breaks — not merely "the model instantiates".
"""

from __future__ import annotations

import pytest

from cyberlab_gen.registries.merge import MergedRegistries
from cyberlab_gen.schemas.attack_spec import PerStepReproducibility
from cyberlab_gen.schemas.enums import (
    CitationKind,
    IdentifierKind,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityTier,
    StepComposition,
)
from cyberlab_gen.schemas.manifest import (
    LabManifest,
    PhaseBlock,
    PhaseImplementation,
    PhaseOutput,
    ProducesWorldState,
    StepBlock,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from cyberlab_gen.schemas.registries import (
    ExecutionContextsRegistry,
    ExternalDataSourcesRegistry,
    FacetEntry,
    FacetsRegistry,
    LabCredentialsRegistry,
    StaticCatalogsRegistry,
    ThesisTypesRegistry,
    ValueTypesRegistry,
)
from cyberlab_gen.validators.base import Finding, FindingResult
from cyberlab_gen.validators.semantic_cross_check_validator import (
    ResponsibleAgent,
    SemanticCrossCheckCode,
    SemanticCrossCheckFinding,
    SemanticCrossCheckResult,
    SemanticCrossCheckValidator,
    references_lab_outputs_findings,
    responsible_agent_for,
)
from tests.unit.framework.pipeline_fakes import make_manifest

# --- builders --------------------------------------------------------------


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _facet(
    name: str,
    *,
    implies: list[str] | None = None,
    incompatible_with: list[str] | None = None,
) -> FacetEntry:
    return FacetEntry(
        name=name,  # type: ignore[arg-type]
        category=name.split(":", 1)[0],  # type: ignore[arg-type]
        proposed_by="extractor",
        description=f"facet {name}",
        applies_at_levels=["lab"],
        implies=implies or [],  # type: ignore[arg-type]
        incompatible_with=incompatible_with or [],  # type: ignore[arg-type]
    )


def _registries(*facets: FacetEntry) -> MergedRegistries:
    return MergedRegistries(
        value_types=ValueTypesRegistry(),
        facets=FacetsRegistry(entries=list(facets)),
        external_data_sources=ExternalDataSourcesRegistry(),
        static_catalogs=StaticCatalogsRegistry(),
        execution_contexts=ExecutionContextsRegistry(),
        lab_credentials=LabCredentialsRegistry(),
        thesis_types=ThesisTypesRegistry(),
    )


def _validator(*facets: FacetEntry) -> SemanticCrossCheckValidator:
    return SemanticCrossCheckValidator(registries=_registries(*facets))


def _manifest_with_facets(facets: list[str]) -> LabManifest:
    return make_manifest().model_copy(update={"facets": facets})


def _step() -> StepBlock:
    return StepBlock(
        id="step-1",  # type: ignore[arg-type]
        step_number=1,
        title="Step 1",
        description=_pstr("do the thing"),
        function_name="step_1",  # type: ignore[arg-type]
        reproducibility=PerStepReproducibility(
            classification=ReproducibilityTier.FULL,
            caveats=_pstr("none"),
            why=_pstr("scriptable"),
        ),
    )


def _phase_with_runtime_pws(*, identifier_source: str, output_names: list[str]) -> PhaseBlock:
    return PhaseBlock(
        id="phase-rt",  # type: ignore[arg-type]
        name="RT phase",
        display_name="1. RT",
        short_description="a phase that produces runtime-named world state",
        step_composition=StepComposition.SEQUENTIAL,
        execution_context="attacker_local",  # type: ignore[arg-type]
        provisioning_mechanism=ProvisioningMechanism.CLI_SCRIPTS,
        outputs=[
            PhaseOutput(name=n, type="github_branch")  # type: ignore[arg-type]
            for n in output_names
        ],
        produces_world_state=[
            ProducesWorldState(
                type="github_branch",  # type: ignore[arg-type]
                identifier_kind=IdentifierKind.RUNTIME_GENERATED,
                identifier_source=identifier_source,
                description=_pstr("runtime-named branch"),
            )
        ],
        steps=[_step()],
        implementation=PhaseImplementation(language="python"),
    )


def _manifest_with_phase(phase: PhaseBlock) -> LabManifest:
    return make_manifest().model_copy(update={"phases": [phase]})


# --- clean path ------------------------------------------------------------


def test_clean_manifest_passes() -> None:
    # facets resolve with no unmet implies / incompatibilities, and the base manifest declares no
    # runtime-generated world state — nothing for the live checks to flag.
    result = _validator(_facet("target:aws")).validate(make_manifest())
    assert result.passed
    assert result.findings == []


# --- facet implies ---------------------------------------------------------


def test_missing_implied_facet_is_flagged() -> None:
    validator = _validator(
        _facet("target:eks", implies=["target:aws", "target:kubernetes"]),
        _facet("target:aws"),
        _facet("target:kubernetes"),
    )
    result = validator.validate(_manifest_with_facets(["target:eks", "target:aws"]))
    assert not result.passed
    finding = next(
        f for f in result.findings if f.code is SemanticCrossCheckCode.MISSING_IMPLIED_FACET
    )
    # detail names both the declaring facet and the missing implied one; the layer does NOT add it.
    assert "target:eks" in finding.detail
    assert "target:kubernetes" in finding.detail


def test_all_implied_facets_present_passes() -> None:
    validator = _validator(
        _facet("target:eks", implies=["target:aws", "target:kubernetes"]),
        _facet("target:aws"),
        _facet("target:kubernetes"),
    )
    result = validator.validate(
        _manifest_with_facets(["target:eks", "target:aws", "target:kubernetes"])
    )
    assert not any(f.code is SemanticCrossCheckCode.MISSING_IMPLIED_FACET for f in result.findings)


# --- facet incompatible_with -----------------------------------------------


def test_incompatible_facets_are_flagged() -> None:
    validator = _validator(
        _facet("runtime:aws", incompatible_with=["target:on_prem_only"]),
        _facet("target:on_prem_only"),
    )
    result = validator.validate(_manifest_with_facets(["runtime:aws", "target:on_prem_only"]))
    assert any(f.code is SemanticCrossCheckCode.INCOMPATIBLE_FACETS for f in result.findings)


def test_incompatibility_detected_from_either_declaring_side_once() -> None:
    # Only runtime:aws lists the incompatibility, and the manifest declares the pair in the reverse
    # order — the check is symmetric and reports each contradictory pair exactly once.
    validator = _validator(
        _facet("target:on_prem_only"),
        _facet("runtime:aws", incompatible_with=["target:on_prem_only"]),
    )
    result = validator.validate(_manifest_with_facets(["target:on_prem_only", "runtime:aws"]))
    incompatible = [
        f for f in result.findings if f.code is SemanticCrossCheckCode.INCOMPATIBLE_FACETS
    ]
    assert len(incompatible) == 1


# --- produces_world_state identifier_source resolution ---------------------


def test_dangling_identifier_source_is_flagged() -> None:
    validator = _validator(_facet("target:aws"))
    phase = _phase_with_runtime_pws(
        identifier_source="phase_outputs.missing", output_names=["branch_name"]
    )
    result = validator.validate(_manifest_with_phase(phase))
    finding = next(
        f for f in result.findings if f.code is SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE
    )
    # the locator is integer-indexed (ADR 0074) so it can feed a targeted re-run / patch.
    assert finding.location == "phases[0].produces_world_state[0].identifier_source"


def test_resolvable_identifier_source_passes() -> None:
    validator = _validator(_facet("target:aws"))
    phase = _phase_with_runtime_pws(
        identifier_source="phase_outputs.branch_name", output_names=["branch_name"]
    )
    result = validator.validate(_manifest_with_phase(phase))
    assert not any(
        f.code is SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE for f in result.findings
    )


def test_identifier_source_without_phase_outputs_prefix_is_flagged() -> None:
    # The documented form is 'phase_outputs.<name>' (schema.md §4.5 / §6.5); a bare output name
    # without the prefix does NOT resolve even when an output of that name exists — pins the prefix
    # requirement (drop the startswith check and this stops flagging).
    validator = _validator(_facet("target:aws"))
    phase = _phase_with_runtime_pws(identifier_source="branch_name", output_names=["branch_name"])
    result = validator.validate(_manifest_with_phase(phase))
    assert any(
        f.code is SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE for f in result.findings
    )


# --- read-only discipline --------------------------------------------------


def test_validator_never_mutates_the_manifest() -> None:
    # Exercise a finding path (an unmet implies) and confirm the input manifest is byte-identical
    # after validation — the layer flags, never authors (``§6.5``, ``§1.6``).
    validator = _validator(_facet("target:eks", implies=["target:aws"]))
    manifest = _manifest_with_facets(["target:eks"])
    before = manifest.model_dump(mode="json", by_alias=True)
    validator.validate(manifest)
    assert manifest.model_dump(mode="json", by_alias=True) == before


# --- inert (Phase-3) code-vs-manifest check --------------------------------


def test_inert_references_lab_outputs_check_is_a_noop() -> None:
    # The references_lab_outputs bidirectional cross-check needs generated IaC (Phase 3); it is built
    # but inert this phase. Pin that it exists and produces no findings until Phase 3 wires it.
    assert references_lab_outputs_findings(make_manifest()) == []


# --- routing seam (consumed by the Task-6 coordinator) ---------------------


def test_live_findings_route_to_the_planner() -> None:
    for code in (
        SemanticCrossCheckCode.MISSING_IMPLIED_FACET,
        SemanticCrossCheckCode.INCOMPATIBLE_FACETS,
        SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE,
    ):
        finding = SemanticCrossCheckFinding(code=code, location="facets[0]", detail="x")
        assert responsible_agent_for(finding) is ResponsibleAgent.PLANNER


def test_reserved_phase3_codes_have_no_route() -> None:
    # The inert code-vs-manifest codes and the vacuous affected_platforms code are not routable in
    # Phase 2 — responsible_agent_for raises loudly rather than guessing an agent (they cannot be
    # produced this phase, so a live finding never carries one).
    for code in (
        SemanticCrossCheckCode.UNDECLARED_LAB_OUTPUT_REFERENCE,
        SemanticCrossCheckCode.UNDECLARED_LAB_RESOURCE_REFERENCE,
        SemanticCrossCheckCode.INCONSISTENT_AFFECTED_PLATFORMS,
    ):
        finding = SemanticCrossCheckFinding(code=code, location="phases[0]", detail="x")
        with pytest.raises(NotImplementedError):
            responsible_agent_for(finding)


# --- ADR-0073 contract conformance -----------------------------------------


def test_finding_and_result_subclass_the_shared_base() -> None:
    assert issubclass(SemanticCrossCheckFinding, Finding)
    assert issubclass(SemanticCrossCheckResult, FindingResult)
