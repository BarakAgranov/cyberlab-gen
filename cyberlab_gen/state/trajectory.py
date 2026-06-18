"""The per-round agent trajectory — typed records of what every agent did, round by round.

Item 1 / ADR 0098. The run store already persists the *final* spec/manifest, the *final*
jury verdict, and cost *counts*; every intermediate round (each producer output, each jury
verdict + feedback, each refine patch) is overwritten in state and survives only in Phoenix
traces, which are opt-in, ephemeral, and UI-only (ADR 0041). This module persists that
intermediate trail to the run directory as files anyone can read on their own.

Shape (the rulings on Item 1):

- ``trajectory.jsonl`` is an **append-only, ordered event stream** of two record kinds.
  Append-only is the most crash-robust streaming form: a halt/crash/Ctrl-C keeps every line
  already written, matching the run store's "always something to read" guarantee (ADR 0039).
- :class:`AgentCallRecord` — emitted at the provider chokepoint when a billed call returns:
  which agent, which round, the structured output, and the input it received.
- :class:`RoutingEventRecord` — emitted by the orchestrator node when it resolves the route
  (revise / approve / route-back / halt). The routing outcome is known only *after* the call's
  line is already written, and append-only means it can't be back-annotated — so it is its own
  ordered event, correlated to the call by round + stage. Reading the stream in order
  reconstructs "producer made X -> jury said revise because Y -> producer changed Z -> approve".
- The "input it received" is captured **deduped by hash**: a large, constant input (the system
  prompt, the blog body) is stored once under ``blobs/`` and referenced by SHA-256, so it is
  not duplicated across rounds. Small structured inputs (rendered feedback) stay inline.

This is pure observation: per ``architecture.md`` §1.5 nothing captured here feeds back into
routing, retry budgets, or stop decisions — it is an audit artifact, not a control input.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, model_validator

from cyberlab_gen.providers import (
    AgentLabel,
    CallOutcome,
    CapabilityHint,
    Message,
    MessageRole,
    ProviderResponse,
    TokenUsage,
    ToolCall,
)
from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.state.run_store import TRAJECTORY_FILENAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from cyberlab_gen.state.run_store import RunHandle

logger = logging.getLogger(__name__)

#: Inline cap for a captured input message. Content longer than this is written once to a
#: content-addressed blob and referenced by hash, so the large constant inputs (the system
#: prompt, the blog body, the manifest) are not duplicated across every round.
_DEFAULT_INLINE_MAX_CHARS = 2000


class TrajectoryRecordKind(StrEnum):
    """Discriminator for a ``trajectory.jsonl`` line — read it first, then parse the line."""

    AGENT_CALL = "agent_call"
    ROUTING_EVENT = "routing_event"


class MessageRef(ArtifactModel):
    """One input message the agent saw, stored inline (small) or by blob hash (large).

    Exactly one of :attr:`content` (inline) or :attr:`content_sha256` (a ``blobs/`` reference)
    is set. An assistant pure-tool-call turn has empty *inline* content (``content=""``) plus
    :attr:`tool_calls`; that is still inline, not a blob.
    """

    role: MessageRole
    content: str | None = None
    content_sha256: str | None = None
    tool_calls: list[ToolCall] = []  # noqa: RUF012 — pydantic field default, not a shared mutable
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one_content_source(self) -> Self:
        has_inline = self.content is not None
        has_blob = self.content_sha256 is not None
        if has_inline == has_blob:
            raise ValueError("MessageRef needs exactly one of content or content_sha256")
        return self


class AgentCallRecord(ArtifactModel):
    """One billed agent call: which agent, which round, what it produced, what it received.

    ``output`` is the structured output ``model_dump`` (it carries the agent's own "why" — the
    jury's rationale + field feedback, the planner/extractor ``llm_inference`` provenance); it is
    ``None`` on a billed-but-raised :attr:`CallOutcome.FAILED` call, whose content is unavailable.
    ``model`` and ``usage`` are the framework-billed values (never the LLM self-report).
    """

    kind: TrajectoryRecordKind = TrajectoryRecordKind.AGENT_CALL
    sequence: int
    round_index: int
    stage: str
    agent: AgentLabel
    capability: CapabilityHint
    outcome: CallOutcome
    model: str
    usage: TokenUsage
    output: dict[str, Any] | None = None
    inputs: list[MessageRef] = []  # noqa: RUF012 — pydantic field default, not a shared mutable


class RoutingEventRecord(ArtifactModel):
    """The framework's routing decision after a round — the outcome dimension of the trajectory.

    Emitted by the orchestrator node, not the agent: ``outcome`` is the resolved route/status
    (e.g. ``"revise"``, ``"approve"``, ``"route_back_to_extractor"``, a terminal status), which
    the LLM never authors. Correlated to its call(s) by ``round_index`` + ``stage``.
    """

    kind: TrajectoryRecordKind = TrajectoryRecordKind.ROUTING_EVENT
    sequence: int
    round_index: int
    stage: str
    outcome: str


class RunTrajectoryRecorder:
    """Captures the per-round agent trajectory to ``trajectory.jsonl`` (ADR 0098).

    One per run, created with the run's :class:`RunHandle`, then fed from two sides:

    - The **provider chokepoint** calls :meth:`record_call` / :meth:`record_failed_call` after
      every billed call — it has the content (structured output, input conversation, usage), and
      satisfies the :class:`~cyberlab_gen.providers.base.TrajectorySink` protocol the
      ``CostRecordingProvider`` notifies.
    - The **orchestrator nodes** call :meth:`enter_stage` before each agent call (to stamp which
      round/stage the calls belong to) and :meth:`routing_event` after they resolve the route
      (the outcome dimension the provider can't see).

    Plain class, not a Pydantic model: it holds mutable per-run state (the sequence counter and
    the current round context), mirroring :class:`~cyberlab_gen.providers.cost_ledger.CostLedger`.
    All writes go through the run handle and are best-effort, so capture never perturbs the run
    (ADR 0041) and a halt/crash keeps every round already written (ADR 0039).
    """

    def __init__(
        self, handle: RunHandle, *, inline_max_chars: int = _DEFAULT_INLINE_MAX_CHARS
    ) -> None:
        self._handle = handle
        self._inline_max_chars = inline_max_chars
        self._sequence = 0
        self._round_index = 0
        self._stage = "start"

    @property
    def handle(self) -> RunHandle:
        """The run handle this recorder writes through."""
        return self._handle

    def enter_stage(self, *, round_index: int, stage: str) -> None:
        """Set the round context that subsequent calls and the next routing event belong to."""
        self._round_index = round_index
        self._stage = stage

    def record_call[T: BaseModel](
        self,
        response: ProviderResponse[T],
        *,
        agent_label: AgentLabel,
        capability: CapabilityHint,
    ) -> None:
        """Record a successful billed call: its structured output and the input it received.

        Best-effort and fully guarded (see :meth:`_emit`): construction + serialization + write.
        """
        self._emit(
            lambda: AgentCallRecord(
                sequence=self._next_sequence(),
                round_index=self._round_index,
                stage=self._stage,
                agent=agent_label,
                capability=capability,
                outcome=CallOutcome.SUCCESS,
                model=response.model,
                usage=response.usage,
                output=response.output.model_dump(mode="json"),
                inputs=self._input_refs(response.conversation),
            )
        )

    def record_failed_call(
        self,
        *,
        model: str,
        usage: TokenUsage,
        agent_label: AgentLabel,
        capability: CapabilityHint,
    ) -> None:
        """Record a billed-but-raised call. Only usage+model are available — no content."""
        self._emit(
            lambda: AgentCallRecord(
                sequence=self._next_sequence(),
                round_index=self._round_index,
                stage=self._stage,
                agent=agent_label,
                capability=capability,
                outcome=CallOutcome.FAILED,
                model=model,
                usage=usage,
            )
        )

    def routing_event(self, outcome: str) -> None:
        """Record the framework's resolved route for the current round (the outcome dimension)."""
        self._emit(
            lambda: RoutingEventRecord(
                sequence=self._next_sequence(),
                round_index=self._round_index,
                stage=self._stage,
                outcome=outcome,
            )
        )

    def _emit(self, build: Callable[[], BaseModel]) -> None:
        """Build and append one trajectory record, guarded (best-effort).

        A capture failure (serialization, a write error, a future record field) must never propagate
        out of the recorder — it is a pure observer (ADR 0039/0041; architecture.md §1.5). The record
        is *built* inside the guard too (not just written), because model construction / serialization
        run before ``RunHandle``'s own ``OSError`` swallow. So capture can never crash a node or a
        paid run, nor — via the provider — skip the catastrophe ceiling.
        """
        try:
            self._handle.append_jsonl(TRAJECTORY_FILENAME, build())
        except Exception:
            logger.warning("trajectory: capture failed; continuing without it", exc_info=True)

    def _next_sequence(self) -> int:
        seq = self._sequence
        self._sequence += 1
        return seq

    def _input_refs(self, conversation: list[Message]) -> list[MessageRef]:
        # Drop the final assistant turn: it is the agent's answer, already captured in `output`.
        messages = conversation[:-1] if conversation else []
        return [self._message_ref(m) for m in messages]

    def _message_ref(self, message: Message) -> MessageRef:
        tool_calls = list(message.tool_calls)
        if len(message.content) > self._inline_max_chars:
            return MessageRef(
                role=message.role,
                content_sha256=self._handle.write_blob(message.content),
                tool_calls=tool_calls,
                tool_call_id=message.tool_call_id,
            )
        return MessageRef(
            role=message.role,
            content=message.content,
            tool_calls=tool_calls,
            tool_call_id=message.tool_call_id,
        )


__all__ = [
    "AgentCallRecord",
    "MessageRef",
    "RoutingEventRecord",
    "RunTrajectoryRecorder",
    "TrajectoryRecordKind",
]
