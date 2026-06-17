"""Planner-Jury stage behavior (Phase 2 Task 4; ADR 0078, 0072).

Drives the Planner-Jury against a mock provider (the Phase-1 jury pattern): it reviews a draft
LabManifest against its AttackSpec and returns a ``JuryVerdict`` (the same shape as the
Extractor-Jury); each verdict fires; it is wired **verify-only** (no ``propose_*`` tools, ADR 0078);
the rubric floor is a 0.7 placeholder exposed for eval calibration (the asymmetric discipline, not
the number).
"""

from __future__ import annotations

import pytest

from cyberlab_gen.agents.extractor_jury import JuryFieldFeedback, JuryScores, JuryVerdict, Verdict
from cyberlab_gen.agents.planner_jury import DEFAULT_RUBRIC_FLOOR, PlannerJury
from cyberlab_gen.providers import (
    AgentLabel,
    CapabilityHint,
    MockProvider,
    ModelRankings,
    ProviderRegistry,
)
from cyberlab_gen.registries.merge import load_merged_registries
from tests.unit.framework.pipeline_fakes import make_manifest, make_spec


def _scores(v: float = 0.9) -> JuryScores:
    return JuryScores(fidelity=v, completeness=v, provenance_correctness=v, structural_validity=v)


def _rankings() -> ModelRankings:
    return ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ]
            }
        }
    )


def _jury(provider: MockProvider) -> PlannerJury:
    return PlannerJury(
        provider=provider,
        registry=ProviderRegistry(_rankings(), frozenset({"anthropic"})),
        registries=load_merged_registries(),
    )


async def _run_with(verdict: JuryVerdict) -> JuryVerdict:
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER_JURY,
        response=verdict,
    )
    return await _jury(provider).review(manifest=make_manifest(), attack_spec=make_spec())


# --- each verdict fires (the LLM judgment reaches the framework) ------------


async def test_jury_approve_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.APPROVE, scores=_scores(0.9), retry_recommended=False, rationale="clean"
        )
    )
    assert out.verdict is Verdict.APPROVE


async def test_jury_revise_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.REVISE,
            scores=_scores(0.65),
            feedback=[
                JuryFieldFeedback(
                    field_path="phases[0].steps[0].description",  # type: ignore[arg-type]
                    problem="orphans a chain step",  # type: ignore[arg-type]
                )
            ],
            retry_recommended=True,
            rationale="one field",
        )
    )
    assert out.verdict is Verdict.REVISE
    assert len(out.feedback) == 1


async def test_jury_reject_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.REJECT,
            scores=_scores(0.2),
            feedback=[
                JuryFieldFeedback(
                    field_path="phases",  # type: ignore[arg-type]
                    problem="drops an entire credential-harvest stage",  # type: ignore[arg-type]
                )
            ],
            retry_recommended=False,
            rationale="major content dropped",
        )
    )
    assert out.verdict is Verdict.REJECT


# --- verify-only wiring (ADR 0078) -----------------------------------------


def test_planner_jury_is_wired_verify_only() -> None:
    # The Planner-Jury reviews; it must not propose — wired verify-only on the shared ToolUsingAgent
    # contract (ADR 0078), inheriting the mechanical read/write split (external_lookup only).
    jury = _jury(MockProvider())
    assert jury._verify_only_tools is True  # pyright: ignore[reportPrivateUsage]


def test_planner_jury_advertises_only_external_lookup() -> None:
    # The verify-only base wiring advertises exactly the read/verify tool, never a propose_* write
    # tool — the §1.5 split enforced by tool availability for the Planner-Jury too.
    from cyberlab_gen.agents.extractor.tools import (
        TOOL_EXTERNAL_LOOKUP,
        TOOL_PROPOSE_VALUE_TYPE,
    )

    jury = _jury(MockProvider())
    tools, _executor = jury._build_tools_and_executor()  # pyright: ignore[reportPrivateUsage]
    names = {t.name for t in tools}
    assert names == {TOOL_EXTERNAL_LOOKUP}
    assert TOOL_PROPOSE_VALUE_TYPE not in names


# --- rubric floor: placeholder + asymmetric-calibration discipline ---------


def test_rubric_floor_default_is_the_placeholder() -> None:
    # 0.7 is a v1 placeholder (architecture.md §8.4), its own constant so the architect calibrates
    # the two juries independently; the number is locked by the eval run (CALIBRATION.md), not here.
    assert DEFAULT_RUBRIC_FLOOR == 0.7
    assert _jury(MockProvider()).rubric_floor == 0.7


def test_rubric_floor_is_validated() -> None:
    with pytest.raises(ValueError, match="rubric_floor"):
        PlannerJury(
            provider=MockProvider(),
            registry=ProviderRegistry(_rankings(), frozenset({"anthropic"})),
            registries=load_merged_registries(),
            rubric_floor=1.5,
        )
