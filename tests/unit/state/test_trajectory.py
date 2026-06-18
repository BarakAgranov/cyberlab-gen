"""Unit tests for the per-round agent trajectory records (Item 1, ADR 0098).

These records are the typed contents of ``trajectory.jsonl`` — one JSON object per
line. They must round-trip losslessly (so the file can be read back and the run
reconstructed) and forbid unknown fields like every other artifact.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from cyberlab_gen.providers import (
    AgentLabel,
    CallOutcome,
    CapabilityHint,
    Message,
    MessageRole,
    ProviderResponse,
    TokenUsage,
)
from cyberlab_gen.state.run_store import BLOBS_DIRNAME, TRAJECTORY_FILENAME, RunKind, RunStore
from cyberlab_gen.state.trajectory import (
    AgentCallRecord,
    MessageRef,
    RoutingEventRecord,
    RunTrajectoryRecorder,
    TrajectoryRecordKind,
)

if TYPE_CHECKING:
    from pathlib import Path


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, cost_usd=Decimal("0.01"))


class _Out(BaseModel):
    """A stand-in for an agent's structured output (e.g. a jury verdict)."""

    model_config = ConfigDict(extra="forbid")

    verdict: str
    rationale: str


def _response(output: _Out, conversation: list[Message]) -> ProviderResponse[_Out]:
    return ProviderResponse(
        output=output,
        raw_text=output.model_dump_json(),
        usage=_usage(),
        model="claude-opus-4-8",
        provider="anthropic",
        conversation=conversation,
    )


def test_agent_call_record_round_trips() -> None:
    record = AgentCallRecord(
        sequence=3,
        round_index=1,
        stage="refine",
        agent=AgentLabel.EXTRACTOR,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        outcome=CallOutcome.SUCCESS,
        model="claude-opus-4-8",
        usage=_usage(),
        output={"attack_spec": {"title": "X"}},
        inputs=[
            MessageRef(role=MessageRole.SYSTEM, content_sha256="abc123"),
            MessageRef(role=MessageRole.USER, content="refine per feedback"),
        ],
    )
    assert record.kind is TrajectoryRecordKind.AGENT_CALL
    reloaded = AgentCallRecord.model_validate_json(record.model_dump_json())
    assert reloaded == record


def test_routing_event_record_round_trips() -> None:
    record = RoutingEventRecord(sequence=4, round_index=1, stage="jury_review", outcome="revise")
    assert record.kind is TrajectoryRecordKind.ROUTING_EVENT
    reloaded = RoutingEventRecord.model_validate_json(record.model_dump_json())
    assert reloaded == record


def test_failed_call_record_has_no_output() -> None:
    # A billed-but-raised call exposes only usage+model; content is unavailable.
    record = AgentCallRecord(
        sequence=1,
        round_index=0,
        stage="plan",
        agent=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        outcome=CallOutcome.FAILED,
        model="claude-opus-4-8",
        usage=_usage(),
    )
    assert record.output is None
    assert record.inputs == []


def test_message_ref_requires_exactly_one_of_content_or_blob() -> None:
    with pytest.raises(ValidationError):
        MessageRef(role=MessageRole.USER)  # neither set
    with pytest.raises(ValidationError):
        MessageRef(role=MessageRole.USER, content="x", content_sha256="abc123")  # both set


def test_message_ref_accepts_empty_inline_content() -> None:
    # An assistant pure-tool-call turn has empty text but is still inline (not blobbed).
    ref = MessageRef(role=MessageRole.ASSISTANT, content="")
    assert ref.content == ""
    assert ref.content_sha256 is None


def test_records_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RoutingEventRecord(sequence=1, round_index=0, stage="x", outcome="approve", bogus=True)  # type: ignore[call-arg]


# --- RunTrajectoryRecorder: capture per-call content + routing events to the run dir ---


def _recorder(tmp_path: Path, *, inline_max_chars: int = 2000) -> RunTrajectoryRecorder:
    handle = RunStore(tmp_path).start(kind=RunKind.PLAN, label="x")
    return RunTrajectoryRecorder(handle, inline_max_chars=inline_max_chars)


def _lines(rec: RunTrajectoryRecorder) -> list[dict[str, Any]]:
    text = (rec.handle.directory / TRAJECTORY_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_record_call_captures_output_and_stamps_round_context(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    rec.enter_stage(round_index=2, stage="jury_review")
    convo = [
        Message(role=MessageRole.SYSTEM, content="the schema"),
        Message(role=MessageRole.USER, content="the manifest"),
        Message(role=MessageRole.ASSISTANT, content='{"verdict":"revise"}'),  # final answer
    ]
    rec.record_call(
        _response(_Out(verdict="revise", rationale="because Y"), convo),
        agent_label=AgentLabel.PLANNER_JURY,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )

    (line,) = _lines(rec)
    assert line["kind"] == "agent_call"
    assert line["round_index"] == 2
    assert line["stage"] == "jury_review"
    assert line["agent"] == "planner_jury"
    assert line["outcome"] == "success"
    assert line["output"]["rationale"] == "because Y"  # the structured "why" is captured
    # the input it received is captured, MINUS the final assistant answer (that is `output`)
    roles = [m["role"] for m in line["inputs"]]
    assert roles == ["system", "user"]


def test_large_input_is_blobbed_and_deduped(tmp_path: Path) -> None:
    rec = _recorder(tmp_path, inline_max_chars=20)
    big = "x" * 500  # a large, constant input (the system prompt / blog body)
    convo = [
        Message(role=MessageRole.SYSTEM, content=big),
        Message(role=MessageRole.USER, content="small"),
        Message(role=MessageRole.ASSISTANT, content="answer"),
    ]
    rec.enter_stage(round_index=0, stage="plan")
    rec.record_call(
        _response(_Out(verdict="planned", rationale="a"), convo),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    rec.enter_stage(round_index=1, stage="refine")
    rec.record_call(  # same big system prompt re-sent next round
        _response(_Out(verdict="planned", rationale="b"), convo),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )

    blobs = list((rec.handle.directory / BLOBS_DIRNAME).glob("*.txt"))
    assert len(blobs) == 1  # the constant large input is stored once, not per round
    assert blobs[0].read_text(encoding="utf-8") == big

    first = _lines(rec)[0]
    system_ref = first["inputs"][0]
    assert system_ref["content"] is None
    assert system_ref["content_sha256"] == blobs[0].stem  # filename is "<sha>.txt"
    small_ref = first["inputs"][1]
    assert small_ref["content"] == "small"  # small content stays inline


def test_routing_event_uses_current_round_context(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    rec.enter_stage(round_index=1, stage="jury_review")
    rec.routing_event("revise")

    (line,) = _lines(rec)
    assert line["kind"] == "routing_event"
    assert line["round_index"] == 1
    assert line["stage"] == "jury_review"
    assert line["outcome"] == "revise"


def test_record_failed_call_has_no_content(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    rec.enter_stage(round_index=0, stage="plan")
    rec.record_failed_call(
        model="claude-opus-4-8",
        usage=_usage(),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    (line,) = _lines(rec)
    assert line["outcome"] == "failed"
    assert line["output"] is None
    assert line["inputs"] == []


def test_recorder_swallows_a_non_oserror_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Best-effort beyond OSError: a capture bug (e.g. a serialization error, not just a disk error)
    # must never propagate out of the recorder and crash a node / a paid run. The orchestrator calls
    # routing_event from inside a node, so a raise there would crash the pipeline.
    from cyberlab_gen.state.run_store import RunHandle

    rec = _recorder(tmp_path)
    rec.enter_stage(round_index=0, stage="plan")

    def _boom(self: RunHandle, name: str, record: object) -> None:
        raise RuntimeError("serialization blew up")

    monkeypatch.setattr(RunHandle, "append_jsonl", _boom)

    rec.routing_event("revise")  # must not raise
    rec.record_failed_call(
        model="m",
        usage=_usage(),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )  # must not raise


def test_sequence_is_monotonic_across_repeated_round_index(tmp_path: Path) -> None:
    # The recorder is reused across drives/feedback re-runs; round_index resets per drive (it is
    # total_iterations) but the monotonic sequence + file order preserve the global story (ADR 0098).
    rec = _recorder(tmp_path)
    rec.enter_stage(round_index=1, stage="plan")
    rec.routing_event("planned")
    rec.enter_stage(round_index=1, stage="plan")  # a second drive restarts round_index at 1
    rec.routing_event("planned")

    lines = _lines(rec)
    assert [line["sequence"] for line in lines] == [0, 1]  # monotonic despite...
    assert [line["round_index"] for line in lines] == [1, 1]  # ...the repeated round index


def test_stream_reconstructs_the_round_by_round_story(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    convo = [
        Message(role=MessageRole.USER, content="in"),
        Message(role=MessageRole.ASSISTANT, content="out"),
    ]

    rec.enter_stage(round_index=0, stage="plan")
    rec.record_call(
        _response(_Out(verdict="planned", rationale="produced X"), convo),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    rec.enter_stage(round_index=0, stage="jury_review")
    rec.record_call(
        _response(_Out(verdict="revise", rationale="because Y"), convo),
        agent_label=AgentLabel.PLANNER_JURY,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    rec.routing_event("revise")
    rec.enter_stage(round_index=1, stage="refine")
    rec.record_call(
        _response(_Out(verdict="planned", rationale="changed Z"), convo),
        agent_label=AgentLabel.PLANNER,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    rec.enter_stage(round_index=1, stage="jury_review")
    rec.record_call(
        _response(_Out(verdict="approve", rationale="ok"), convo),
        agent_label=AgentLabel.PLANNER_JURY,
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
    )
    rec.routing_event("approve")

    lines = _lines(rec)
    assert [line["kind"] for line in lines] == [
        TrajectoryRecordKind.AGENT_CALL,
        TrajectoryRecordKind.AGENT_CALL,
        TrajectoryRecordKind.ROUTING_EVENT,
        TrajectoryRecordKind.AGENT_CALL,
        TrajectoryRecordKind.AGENT_CALL,
        TrajectoryRecordKind.ROUTING_EVENT,
    ]
    assert [line["sequence"] for line in lines] == [0, 1, 2, 3, 4, 5]  # a single total order
    assert lines[0]["output"]["rationale"] == "produced X"
    assert lines[1]["output"]["rationale"] == "because Y"
    assert lines[2]["outcome"] == "revise"
    assert lines[3]["output"]["rationale"] == "changed Z"
    assert lines[5]["outcome"] == "approve"
