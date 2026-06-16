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
    Planner,
    PlannerToolExecutor,
    PlanResult,
    planner_tool_definitions,
)
from cyberlab_gen.agents.extractor.tools import (
    TOOL_EXTERNAL_LOOKUP,
    TOOL_PROPOSE_VALUE_TYPE,
    extractor_tool_definitions,
)
from cyberlab_gen.agents.planner.planner import DEFAULT_PLANNER_MAX_TOKENS
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
from tests.unit.framework.pipeline_fakes import make_manifest, make_spec

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


def _register(provider: MockProvider, manifest: LabManifest) -> None:
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=manifest,
    )


# --- the stage: Planner.plan -----------------------------------------------


async def test_plan_returns_structurally_valid_manifest() -> None:
    provider = MockProvider()
    _register(provider, make_manifest())
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    assert isinstance(result, PlanResult)
    manifest = result.manifest
    # The available "Layer-1" for a manifest this phase is structural validity + round-trip
    # (full manifest Layer-1/Layer-2 lands in Task 5/6). It must survive a re-validate and a
    # YAML round-trip to an equal instance.
    assert LabManifest.model_validate(manifest.model_dump(mode="json", by_alias=True)) == manifest
    assert LabManifest.from_yaml(manifest.to_yaml()) == manifest


async def test_plan_derives_lab_level_reproducibility_overwriting_llm_value() -> None:
    # The canned manifest carries core.reproducibility = FULL (with prose) — a deliberately WRONG
    # stand-in. The spec's chain spans tiers, so the framework-derived lab level is `mixed`.
    provider = MockProvider()
    _register(provider, make_manifest(step_tiers=[FULL, DEMO]))
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    repro = result.manifest.core.reproducibility
    assert (
        repro.classification_lab_level is ReproducibilityLabLevel.MIXED
    )  # derived, not the FULL emitted
    # The framework owns the whole block (ADR 0088/0090): the LLM's prose assessment is dropped.
    assert repro.overall_assessment is None
    assert repro.derivation_trace  # the derive populated the trace


async def test_plan_carries_per_step_reproducibility_unchanged() -> None:
    provider = MockProvider()
    _register(provider, make_manifest(step_tiers=[FULL, DEMO]))
    result = await _planner(provider).plan(_spec([FULL, DEMO]))

    tiers = [s.reproducibility.classification for p in result.manifest.phases for s in p.steps]
    # The finalize touches only the lab-level block; per-step content (StepBlock.reproducibility)
    # is the carried-forward tier, untouched (`§0.7`).
    assert tiers == [FULL, DEMO]


async def test_plan_resolves_model_via_capability_not_hardcoded() -> None:
    provider = MockProvider()
    _register(provider, make_manifest())
    await _planner(provider, hqr_model="ranked-hqr-xyz").plan(_spec([FULL]))

    # The runner resolved HIGH_QUALITY_REASONING -> the ranked model; the Planner names no model.
    # (Reaching the mock at all also proves the Planner requested HIGH_QUALITY_REASONING — a wrong
    # capability would not match and would raise UnmatchedMockCall.)
    assert provider.last_model == "ranked-hqr-xyz"


async def test_plan_passes_the_output_token_budget() -> None:
    provider = MockProvider()
    _register(provider, make_manifest())
    await _planner(provider).plan(_spec([FULL]))
    # The Planner emits the whole manifest as one tool call; the output cap must reach the provider
    # (the truncation class the Extractor's budget guards, recalibrated there).
    assert provider.last_max_tokens == DEFAULT_PLANNER_MAX_TOKENS


def test_output_schema_rejects_untyped_input() -> None:
    # The Planner's output contract (LabManifest) forbids an untyped input — there is no untyped
    # fallback (`agents.md §5.7` quality bar): InputBlock.type is required.
    data = make_manifest().model_dump(mode="json", by_alias=True)
    del data["inputs"][0]["type"]
    with pytest.raises(ValidationError):
        LabManifest.model_validate(data)


# --- the producer tool set (ADR 0089) --------------------------------------


def test_planner_tool_set_is_external_lookup_only() -> None:
    names = {t.name for t in planner_tool_definitions(["nvd"])}
    assert names == {TOOL_EXTERNAL_LOOKUP}  # producer read set; no propose_* this slice


def test_planner_tool_set_excludes_value_type_proposals_the_extractor_keeps() -> None:
    # Regression pinning the two inventories: the Extractor (unchanged) advertises value-type
    # proposals; the Planner never does (Extractor authority, schema.md §4.16).
    extractor_names = {t.name for t in extractor_tool_definitions(["nvd"])}
    planner_names = {t.name for t in planner_tool_definitions(["nvd"])}
    assert TOOL_PROPOSE_VALUE_TYPE in extractor_names
    assert TOOL_PROPOSE_VALUE_TYPE not in planner_names


async def test_planner_executor_serves_lookup_and_refuses_proposals() -> None:
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

    refused = await executor.execute(
        ToolCall(
            call_id="2",
            tool_name=TOOL_PROPOSE_VALUE_TYPE,
            arguments={"name": "x", "description": "y", "reasoning": "z"},
        )
    )
    assert refused.is_error is True  # value-type proposals are refused mechanically (read-only)
