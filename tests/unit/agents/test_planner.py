"""Planner stage behavior (Phase 2 Task 3; ADR 0089/0090).

Drives the Planner against a mock provider (the Phase-1 pattern): it produces a
structurally-valid ``LabManifest`` from a fixture AttackSpec, the framework **derives** the
lab-level ``core.reproducibility`` (overwriting the LLM's value — ADR 0090), per-step tiers are
carried through unchanged (``§0.7``), the model is resolved via a capability hint (no hardcoded
model), the output schema forbids an untyped input (the quality bar), and the Planner's producer
tool set is ``external_lookup`` only — no value-type proposals (Extractor authority).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cyberlab_gen.agents import (
    JuryFieldFeedback,
    PlanAttempt,
    Planner,
    PlannerRefusal,
    PlannerToolExecutor,
    PlanOutcome,
    PlanResult,
    ProposedFacet,
    planner_tool_definitions,
)
from cyberlab_gen.agents.extractor.tools import (
    TOOL_EXTERNAL_LOOKUP,
    TOOL_PROPOSE_FACET,
    TOOL_PROPOSE_THESIS_TYPE,
    TOOL_PROPOSE_VALUE_TYPE,
    extractor_tool_definitions,
)
from cyberlab_gen.agents.planner.planner import (
    DEFAULT_PATCH_RETRY_ATTEMPTS,
    DEFAULT_PLANNER_MAX_TOKENS,
)
from cyberlab_gen.agents.planner.tools import TOOL_QUERY_VALUE_TYPES
from cyberlab_gen.errors import PlanningError
from cyberlab_gen.framework.refinement import FieldPatch, RefinementPatch
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint, ToolCall
from cyberlab_gen.providers.mock_provider import MockProvider
from cyberlab_gen.providers.ranking import ModelRankings, ProviderRegistry
from cyberlab_gen.registries.merge import load_merged_registries
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    PerStepReproducibility,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityLabLevel,
    ReproducibilityTier,
)
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from tests.unit.framework.pipeline_fakes import (
    make_cannot_plan_attempt,
    make_manifest,
    make_plan_attempt,
    make_route_back_attempt,
    make_spec,
)

FULL = ReproducibilityTier.FULL
DEMO = ReproducibilityTier.DEMONSTRATION_ONLY


# --- builders / harness ----------------------------------------------------


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _cstep(num: int, tier: ReproducibilityTier) -> ChainStep:
    return ChainStep(
        id=f"step-{num}",  # type: ignore[arg-type]
        step_number=num,
        title=f"Step {num}",
        description=_pstr("do the thing"),
        blog_excerpt="verbatim",
        techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
        reproducibility=PerStepReproducibility(
            classification=tier, caveats=_pstr("c"), why=_pstr("w")
        ),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _spec(tiers: list[ReproducibilityTier]) -> AttackSpec:
    """An in-scope AttackSpec whose canonical chain carries ``tiers``."""
    steps = [_cstep(i + 1, t) for i, t in enumerate(tiers)]
    return make_spec().model_copy(update={"chain": ChainBlock(chain_steps=steps)})


def _rankings(*, hqr_model: str = "ranked-hqr-model") -> ModelRankings:
    return ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": hqr_model}
                ],
                CapabilityHint.LONG_CONTEXT_EXTRACTION.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ],
            }
        }
    )


def _planner(provider: MockProvider, *, hqr_model: str = "ranked-hqr-model") -> Planner:
    return Planner(
        provider=provider,
        registry=ProviderRegistry(_rankings(hqr_model=hqr_model), frozenset({"anthropic"})),
        registries=load_merged_registries(),
    )


def _register(provider: MockProvider, attempt: PlanAttempt) -> None:
    """Register the Planner's forced output (a PlanAttempt) for the plan capability."""
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=attempt,
    )


def _register_patch(provider: MockProvider, patch: RefinementPatch) -> None:
    """Register a RefinementPatch for the refine path (same capability/label, RefinementPatch out)."""
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=patch,
    )


# --- the stage: Planner.plan -----------------------------------------------


async def test_plan_returns_structurally_valid_manifest() -> None:
    provider = MockProvider()
    _register(provider, make_plan_attempt())
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    assert isinstance(result, PlanResult)
    assert result.outcome is PlanOutcome.PLANNED
    manifest = result.manifest
    assert manifest is not None  # PLANNED carries a manifest (the PlanAttempt validator guarantees)
    # The available "Layer-1" for a manifest this phase is structural validity + round-trip
    # (full manifest Layer-1/Layer-2 lands in Task 5/6). It must survive a re-validate and a
    # YAML round-trip to an equal instance.
    assert LabManifest.model_validate(manifest.model_dump(mode="json", by_alias=True)) == manifest
    assert LabManifest.from_yaml(manifest.to_yaml()) == manifest


async def test_plan_derives_lab_level_reproducibility_overwriting_llm_value() -> None:
    # The canned manifest carries core.reproducibility = FULL (with prose) — a deliberately WRONG
    # stand-in. The spec's chain spans tiers, so the framework-derived lab level is `mixed`.
    provider = MockProvider()
    _register(provider, make_plan_attempt(step_tiers=[FULL, DEMO]))
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    assert result.manifest is not None
    repro = result.manifest.core.reproducibility
    assert (
        repro.classification_lab_level is ReproducibilityLabLevel.MIXED
    )  # derived, not the FULL emitted
    # The framework owns the whole block (ADR 0088/0090): the LLM's prose assessment is dropped.
    assert repro.overall_assessment is None
    assert repro.derivation_trace  # the derive populated the trace


async def test_plan_carries_per_step_reproducibility_unchanged() -> None:
    provider = MockProvider()
    _register(provider, make_plan_attempt(step_tiers=[FULL, DEMO]))
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    assert result.manifest is not None
    tiers = [s.reproducibility.classification for p in result.manifest.phases for s in p.steps]
    # The finalize touches only the lab-level block; per-step content (StepBlock.reproducibility)
    # is the carried-forward tier, untouched (`§0.7`).
    assert tiers == [FULL, DEMO]


async def test_plan_resolves_model_via_capability_not_hardcoded() -> None:
    provider = MockProvider()
    _register(provider, make_plan_attempt())
    await _planner(provider, hqr_model="ranked-hqr-xyz").plan(_spec([FULL]))

    # The runner resolved HIGH_QUALITY_REASONING -> the ranked model; the Planner names no model.
    # (Reaching the mock at all also proves the Planner requested HIGH_QUALITY_REASONING — a wrong
    # capability would not match and would raise UnmatchedMockCall.)
    assert provider.last_model == "ranked-hqr-xyz"


async def test_plan_passes_the_output_token_budget() -> None:
    provider = MockProvider()
    _register(provider, make_plan_attempt())
    await _planner(provider).plan(_spec([FULL]))
    # The Planner emits the whole manifest as one tool call; the output cap must reach the provider
    # (the truncation class the Extractor's budget guards, recalibrated there).
    assert provider.last_max_tokens == DEFAULT_PLANNER_MAX_TOKENS


# --- route-back / cannot_plan outcomes (ADR 0092) --------------------------


async def test_plan_surfaces_attackspec_incoherence_as_route_back() -> None:
    # §5.7: the Planner detects AttackSpec incoherence and FLAGS it (no manifest); it must NOT
    # repair the AttackSpec. The framework routes this back to the Extractor (coordinator, Task 4).
    provider = MockProvider()
    _register(provider, make_route_back_attempt())
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    assert result.outcome is PlanOutcome.ATTACKSPEC_INCOHERENT
    assert result.manifest is None  # the Planner produced no manifest — it flagged, did not fix
    assert result.refusal is not None
    assert result.refusal.attack_spec_field_paths  # the structured detail the route-back carries


async def test_plan_surfaces_cannot_plan() -> None:
    provider = MockProvider()
    _register(provider, make_cannot_plan_attempt())
    result = await _planner(provider).plan(_spec([FULL]))

    assert result.outcome is PlanOutcome.CANNOT_PLAN
    assert result.manifest is None
    assert result.refusal is not None


def test_plan_attempt_validator_couples_outcome_and_payload() -> None:
    # The discriminator↔payload coupling fails structurally rather than mis-routing control flow.
    with pytest.raises(ValidationError):
        PlanAttempt(outcome=PlanOutcome.PLANNED, manifest=None)  # planned needs a manifest
    with pytest.raises(ValidationError):
        # incoherent must not carry a manifest
        PlanAttempt(
            outcome=PlanOutcome.ATTACKSPEC_INCOHERENT,
            manifest=make_manifest(),
            refusal=PlannerRefusal(
                summary="x",  # type: ignore[arg-type]
                attack_spec_field_paths=["chain"],  # type: ignore[arg-type]
                detail="y",  # type: ignore[arg-type]
            ),
        )


# --- the refine (Planner-Jury revise) targeted-patch path (ADR 0054/0091/0092) ---


def _step_desc_patch(value: str) -> RefinementPatch:
    new_desc = _pstr(value).model_dump(mode="json", by_alias=True)
    return RefinementPatch(
        patches=[FieldPatch(field_path="phases[0].steps[0].description", new_value=new_desc)]
    )


async def test_refine_targeted_patch_leaves_unflagged_fields_identical() -> None:
    # A jury revise drives a patch of ONLY the flagged manifest path; every other field stays
    # byte-identical (convergence by construction, now for the manifest).
    provider = MockProvider()
    _register_patch(provider, _step_desc_patch("a corrected step description"))
    prior = make_manifest(step_tiers=[FULL, DEMO])
    spec = _spec([FULL, DEMO])
    feedback = [
        JuryFieldFeedback(
            field_path="phases[0].steps[0].description",  # type: ignore[arg-type]
            problem="too vague",  # type: ignore[arg-type]
        )
    ]
    result = await _planner(provider).refine(
        prior_manifest=prior, attack_spec=spec, feedback=feedback
    )

    assert result.outcome is PlanOutcome.PLANNED
    assert result.manifest is not None
    patched = result.manifest
    assert patched.phases[0].steps[0].description.value == "a corrected step description"
    # Non-regression: overwrite ONLY the flagged path AND the framework-derived lab-level block in
    # the prior dump, then the dumps must be byte-identical — proof nothing else moved.
    prior_dump = prior.model_dump(mode="json", by_alias=True)
    patched_dump = patched.model_dump(mode="json", by_alias=True)
    prior_dump["phases"][0]["steps"][0]["description"] = patched_dump["phases"][0]["steps"][0][  # type: ignore[index]
        "description"
    ]
    prior_dump["core"]["reproducibility"] = patched_dump["core"]["reproducibility"]  # type: ignore[index]
    assert patched_dump == prior_dump


async def test_refine_re_derives_core_reproducibility() -> None:
    # The patch does not touch reproducibility; the prior manifest's core block is a wrong stand-in
    # (FULL). refine re-derives it from the spec's tiers (MIXED) — the field stays authoritative on
    # the refine path too (guard every path, ADR 0085/0090).
    provider = MockProvider()
    _register_patch(provider, _step_desc_patch("sharper"))
    prior = make_manifest(step_tiers=[FULL, DEMO])
    result = await _planner(provider).refine(
        prior_manifest=prior,
        attack_spec=_spec([FULL, DEMO]),
        feedback=[
            JuryFieldFeedback(
                field_path="phases[0].steps[0].description",  # type: ignore[arg-type]
                problem="vague",  # type: ignore[arg-type]
            )
        ],
    )
    assert result.manifest is not None
    assert (
        result.manifest.core.reproducibility.classification_lab_level
        is ReproducibilityLabLevel.MIXED
    )


async def test_refine_rejects_patch_targeting_framework_owned_repro_then_halts() -> None:
    # A patch that targets the framework-owned core.reproducibility is rejected by the resolver
    # (ADR 0091); the bounded re-prompt loop never converges on it and halts cleanly with
    # PlanningError (R1) — never an unbounded spin, never a forged framework block.
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(patches=[FieldPatch(field_path="core.reproducibility", new_value=None)]),
    )
    with pytest.raises(PlanningError):
        await _planner(provider).refine(
            prior_manifest=make_manifest(),
            attack_spec=_spec([FULL]),
            feedback=[
                JuryFieldFeedback(
                    field_path="core.reproducibility",  # type: ignore[arg-type]
                    problem="should be mixed",  # type: ignore[arg-type]
                )
            ],
        )


def test_refine_patch_retry_budget_is_a_placeholder() -> None:
    # The bounded re-prompt budget is a v1 placeholder (architecture.md §8.4), exposed so the eval
    # harness can find it; pinned so a silent change is visible.
    assert DEFAULT_PATCH_RETRY_ATTEMPTS == 2


def test_output_schema_rejects_untyped_input() -> None:
    # The Planner's output contract (LabManifest) forbids an untyped input — there is no untyped
    # fallback (`agents.md §5.7` quality bar): InputBlock.type is required.
    data = make_manifest().model_dump(mode="json", by_alias=True)
    del data["inputs"][0]["type"]
    with pytest.raises(ValidationError):
        LabManifest.model_validate(data)


# --- the producer tool set (Task 7 / ADR 0099) -----------------------------


def test_planner_tool_set_is_producer_read_plus_scoped_propose_facet() -> None:
    # Task 7: the producer set is the read tools (external_lookup, query_value_types_registry) plus
    # the Planner's OWN scoped propose_facet — but never propose_value_type / propose_thesis_type
    # (Extractor authority, schema.md §4.16).
    names = {t.name for t in planner_tool_definitions(["nvd"])}
    assert names == {TOOL_EXTERNAL_LOOKUP, TOOL_PROPOSE_FACET, TOOL_QUERY_VALUE_TYPES}


def test_planner_propose_facet_advertises_only_planner_categories() -> None:
    propose = next(t for t in planner_tool_definitions(["nvd"]) if t.name == TOOL_PROPOSE_FACET)
    enum = propose.input_schema["properties"]["category"]["enum"]
    assert set(enum) == {
        "runtime",
        "lab_class_signal",
    }  # the Planner's authority, not the Extractor's


def test_planner_tool_set_excludes_the_proposals_the_extractor_keeps() -> None:
    # Regression pinning the two inventories: the Extractor (unchanged) advertises value-type /
    # thesis-type proposals; the Planner never does (Extractor authority, schema.md §4.16).
    extractor_names = {t.name for t in extractor_tool_definitions(["nvd"])}
    planner_names = {t.name for t in planner_tool_definitions(["nvd"])}
    assert {TOOL_PROPOSE_VALUE_TYPE, TOOL_PROPOSE_THESIS_TYPE} <= extractor_names
    assert TOOL_PROPOSE_VALUE_TYPE not in planner_names
    assert TOOL_PROPOSE_THESIS_TYPE not in planner_names


async def test_planner_executor_serves_external_lookup() -> None:
    executor = PlannerToolExecutor(registries=load_merged_registries(), nvd_client=None)
    lookup = await executor.execute(
        ToolCall(
            call_id="1",
            tool_name=TOOL_EXTERNAL_LOOKUP,
            arguments={"source_id": "nvd", "params": {"cve_id": "CVE-2024-1"}},
        )
    )
    assert lookup.is_error is False  # read-only lookup served (no client -> recorded not-found)
    assert len(executor.lookups) == 1


async def test_planner_executor_records_runtime_facet_proposal() -> None:
    # Task 7: the Planner proposes runtime:* facets (its authority). The executor collects them on
    # the inherited facet_proposals side-channel — captured for the framework, not promoted here.
    executor = PlannerToolExecutor(registries=load_merged_registries(), nvd_client=None)
    result = await executor.execute(
        ToolCall(
            call_id="1",
            tool_name=TOOL_PROPOSE_FACET,
            arguments={
                "name": "runtime:codebuild_project",
                "category": "runtime",
                "description": "A CodeBuild project provisioned for the lab runtime.",
                "applies_at_levels": ["lab"],
                "reasoning": "the planned lab needs a runtime facet not in the bundled registry",
            },
        )
    )
    assert result.is_error is False
    assert len(executor.facet_proposals) == 1
    assert executor.facet_proposals[0].category == "runtime"


async def test_planner_executor_refuses_value_type_and_thesis_type_proposals() -> None:
    # The Planner is a producer, but value_types / thesis_types are the Extractor's authority alone
    # (schema.md §4.16): those write tools are refused mechanically (defense-in-depth behind the
    # withheld advertisements).
    executor = PlannerToolExecutor(registries=load_merged_registries(), nvd_client=None)
    for tool in (TOOL_PROPOSE_VALUE_TYPE, TOOL_PROPOSE_THESIS_TYPE):
        refused = await executor.execute(
            ToolCall(
                call_id="x",
                tool_name=tool,
                arguments={"name": "x", "description": "y", "reasoning": "z"},
            )
        )
        assert refused.is_error is True
    assert executor.value_type_proposals == []
    assert executor.thesis_type_proposals == []


async def test_planner_executor_rejects_facet_outside_its_authority() -> None:
    # A target:* facet is the Extractor's authority; the Planner's category gate rejects it (dropped,
    # not recorded) — but never as a fatal error result (ADR 0043).
    executor = PlannerToolExecutor(registries=load_merged_registries(), nvd_client=None)
    result = await executor.execute(
        ToolCall(
            call_id="1",
            tool_name=TOOL_PROPOSE_FACET,
            arguments={
                "name": "target:eks",
                "category": "target",
                "description": "targets an EKS cluster",
                "applies_at_levels": ["lab"],
                "reasoning": "blog attacks an EKS control plane",
            },
        )
    )
    assert result.is_error is False
    assert "not recorded" in result.content
    assert executor.facet_proposals == []


async def test_planner_executor_query_value_types_returns_registry_shapes() -> None:
    # query_value_types_registry is a read tool (deferred from Task 3): it returns value_types shapes
    # on demand so the Planner can pick/shape-search. Read-only, never fatal.
    registries = load_merged_registries()
    executor = PlannerToolExecutor(registries=registries, nvd_client=None)
    listing = await executor.execute(
        ToolCall(call_id="1", tool_name=TOOL_QUERY_VALUE_TYPES, arguments={})
    )
    assert listing.is_error is False
    # A named lookup of a registered value type returns that entry's detail.
    known = next(iter(registries.value_types.entries), None)
    if known is not None:
        named = await executor.execute(
            ToolCall(call_id="2", tool_name=TOOL_QUERY_VALUE_TYPES, arguments={"name": known.name})
        )
        assert named.is_error is False
        assert known.name in named.content


# --- base prompt teaches the identifier_source form (follow-up to ADR 0105) -


def test_planner_base_prompt_teaches_phase_outputs_identifier_source_form() -> None:
    # The semantic cross-check requires a runtime_generated identifier_source of the form
    # `phase_outputs.<name>` resolving to a declared phase output; without prompt guidance the Planner
    # emits a bare output name and burns a refinement round every run. Pin that the base prompt
    # teaches the form so the guidance cannot be silently dropped.
    from cyberlab_gen.agents import load_prompt
    from cyberlab_gen.agents.planner.planner import PLANNER_AGENT_DIR

    prompt = load_prompt(PLANNER_AGENT_DIR)
    assert "phase_outputs.<name>" in prompt
    assert "identifier_kind" in prompt
    assert "runtime_generated" in prompt


def test_plan_result_carries_facet_proposals_captured_not_promoted() -> None:
    # Task 7: PlanResult gains a facet_proposals side-channel (captured, not promoted — Task 8).
    result = PlanResult(
        outcome=PlanOutcome.PLANNED,
        manifest=make_manifest(),
        lookups=[],
        facet_proposals=[
            ProposedFacet(
                name="runtime:codebuild_project",
                category="runtime",
                description="A CodeBuild project.",
                applies_at_levels=["lab"],
                reasoning="needed at runtime",
            )
        ],
    )
    assert [f.name for f in result.facet_proposals] == ["runtime:codebuild_project"]


async def test_plan_surfaces_executor_facet_proposals_field() -> None:
    # The plan path threads executor.facet_proposals onto PlanResult (same wiring as lookups). The
    # MockProvider does not drive the tool loop, so the captured list is empty here — pinning that the
    # field is populated from the executor (not omitted), with capture covered by the executor tests.
    provider = MockProvider()
    _register(provider, make_plan_attempt())
    result = await _planner(provider).plan(_spec([FULL, DEMO]))
    assert result.facet_proposals == []
