"""End-to-end trajectory capture across both pipelines (Item 1, ADR 0098).

Two layers:

- **Orchestrator wiring** — drive the real extract / plan graphs with the shared agent fakes and a
  real :class:`RunTrajectoryRecorder`, then read ``trajectory.jsonl`` back. The fakes bypass the
  provider, so these assert the orchestrator-side half (``routing_event`` lines stamped with the
  right round/stage/outcome) — the producer→jury→outcome story, round by round.
- **Runner glue** — ``enable_trajectory`` wires the recorder to the shared ``CostRecordingProvider``
  so a billed call writes the content half (an ``agent_call`` line). This covers the production
  attachment the fake-agent tests don't exercise.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel

from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, Verdict
from cyberlab_gen.framework.orchestrator import PipelineState, PipelineStatus, build_pipeline
from cyberlab_gen.framework.plan_orchestrator import (
    PlanPipelineState,
    PlanPipelineStatus,
    build_plan_pipeline,
)
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    ProviderResponse,
    TokenUsage,
)
from cyberlab_gen.providers.cost_ledger import CostLedger
from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
from cyberlab_gen.state.run_store import TRAJECTORY_FILENAME, RunKind, RunStore
from cyberlab_gen.state.trajectory import RunTrajectoryRecorder
from tests.unit.framework.pipeline_fakes import (
    FakeCrossCheckValidator,
    FakeExtractor,
    FakeJury,
    FakePlanner,
    FakePlannerJury,
    make_manifest,
    make_plan_result,
    make_spec,
    make_validator,
    make_verdict,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.providers.base import Message, ToolDefinition, ToolExecutor


def _routing(handle_dir: Path) -> list[tuple[object, object, object]]:
    text = (handle_dir / TRAJECTORY_FILENAME).read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    return [
        (line["stage"], line["outcome"], line["round_index"])
        for line in lines
        if line["kind"] == "routing_event"
    ]


async def test_extract_pipeline_records_trajectory(tmp_path: Path) -> None:
    handle = RunStore(tmp_path).start(kind=RunKind.EXTRACT, label="x")
    recorder = RunTrajectoryRecorder(handle)
    ext = FakeExtractor([make_spec(facets=["target:aws"])])
    jury = FakeJury([make_verdict(Verdict.APPROVE)])

    run = build_pipeline(extractor=ext, validator=make_validator(), jury=jury, recorder=recorder)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    # The jury's verdict is the agent-decision outcome of round 1 (the producer's agent_call content
    # comes from the provider, which the fake extractor bypasses).
    assert _routing(handle.directory) == [("jury_review", "approve", 1)]


async def test_plan_pipeline_records_round_by_round_story(tmp_path: Path) -> None:
    handle = RunStore(tmp_path).start(kind=RunKind.PLAN, label="x")
    recorder = RunTrajectoryRecorder(handle)
    # plan -> jury revise -> refine -> jury approve -> cross-check pass
    planner = FakePlanner(
        [make_plan_result()], refine_results=[make_plan_result(make_manifest(step_tiers=None))]
    )
    revise = make_verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path="phases[0].steps[0].description", problem="vague")],
    )
    jury = FakePlannerJury([revise, make_verdict(Verdict.APPROVE)])

    run = build_plan_pipeline(
        planner=planner, jury=jury, validator=FakeCrossCheckValidator(), recorder=recorder
    )
    state = await run(PlanPipelineState(attack_spec=make_spec()))

    assert state.status is PlanPipelineStatus.PLANNED
    # The whole story is reconstructable from the ordered routing stream, round by round.
    assert _routing(handle.directory) == [
        ("plan", "planned", 1),
        ("jury_review", "revise", 1),
        ("refine", "planned", 2),
        ("jury_review", "approve", 2),
        ("semantic_cross_check", "cross_check_pass", 2),
    ]


class _Out(BaseModel):
    ok: bool = True


class _FixedInnerProvider:
    """A minimal ``Provider`` returning a fixed structured response — for the runner-glue test.

    Mirrors ``test_spend_guards._FakeInnerProvider``: the methods return ``object`` (the call site
    passes it with ``# type: ignore[arg-type]``), so the invariant ``ProviderResponse`` generic does
    not need to be threaded through a fake.
    """

    @property
    def name(self) -> str:
        return "fixed"

    def _response(self) -> object:
        return ProviderResponse[_Out](
            output=_Out(),
            raw_text="{}",
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost_usd=Decimal("0.01")),
            model="claude-opus-4-8",
            provider="fixed",
            conversation=[],
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        model: str,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> object:
        del messages, output_schema, capability, model, agent_label, max_tokens
        return self._response()

    async def complete_with_tools(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        model: str,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> object:  # pragma: no cover - not exercised
        del messages, output_schema, capability, model, tools, tool_executor
        del agent_label, max_iterations, max_tokens
        return self._response()


async def test_plan_runner_enable_trajectory_attaches_provider_sink(tmp_path: Path) -> None:
    # The production glue: enable_trajectory wires the recorder to the shared provider, so a billed
    # call writes the content half (an agent_call line) into the run dir. (The fake-agent
    # orchestrator tests above bypass the provider, so this is the piece they don't cover.)
    from cyberlab_gen.cli.plan import PipelinePlanRunner

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(_FixedInnerProvider(), ledger)  # type: ignore[arg-type]
    runner = PipelinePlanRunner(
        planner=FakePlanner([make_plan_result()]),  # type: ignore[arg-type]
        jury=FakePlannerJury([make_verdict(Verdict.APPROVE)]),  # type: ignore[arg-type]
        validator=FakeCrossCheckValidator(),  # type: ignore[arg-type]
        provider=provider,
    )
    handle = RunStore(tmp_path).start(kind=RunKind.PLAN, label="x")

    runner.enable_trajectory(handle)
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        model="claude-opus-4-8",
        agent_label=AgentLabel.PLANNER,
    )

    text = (handle.directory / TRAJECTORY_FILENAME).read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == 1
    assert lines[0]["kind"] == "agent_call"
    assert lines[0]["agent"] == "planner"
    assert lines[0]["outcome"] == "success"


async def test_extract_runner_enable_trajectory_attaches_provider_sink(tmp_path: Path) -> None:
    # The extract runner's glue is symmetric to the plan runner's (both wire the recorder to the
    # shared provider at run start) — pin it so a regression dropping the wiring is caught.
    from cyberlab_gen.cli.extract import PipelineExtractRunner

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(_FixedInnerProvider(), ledger)  # type: ignore[arg-type]
    runner = PipelineExtractRunner(
        extractor=object(),  # type: ignore[arg-type]
        validator=object(),  # type: ignore[arg-type]
        jury=object(),  # type: ignore[arg-type]
        provider=provider,
    )
    handle = RunStore(tmp_path).start(kind=RunKind.EXTRACT, label="x")

    runner.enable_trajectory(handle)
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        model="claude-opus-4-8",
        agent_label=AgentLabel.EXTRACTOR,
    )

    lines = (handle.directory / TRAJECTORY_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["agent"] == "extractor"


async def test_enable_trajectory_is_a_safe_no_op_without_a_provider(tmp_path: Path) -> None:
    # A fake/provider-less runner (the test seam) must no-op enable_trajectory, not crash.
    from cyberlab_gen.cli.plan import PipelinePlanRunner

    runner = PipelinePlanRunner(
        planner=FakePlanner([make_plan_result()]),  # type: ignore[arg-type]
        jury=FakePlannerJury([make_verdict(Verdict.APPROVE)]),  # type: ignore[arg-type]
        validator=FakeCrossCheckValidator(),  # type: ignore[arg-type]
    )  # no provider
    handle = RunStore(tmp_path).start(kind=RunKind.PLAN, label="x")
    runner.enable_trajectory(handle)  # must not raise
    assert not (handle.directory / TRAJECTORY_FILENAME).exists()  # nothing wired, nothing written
