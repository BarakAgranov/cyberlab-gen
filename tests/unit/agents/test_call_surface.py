"""Tests for the agent call surface (capability-hint dispatch).

Covers the Task 2 exit criteria:
- a capability hint resolves to a reachable model and skips unreachable ones;
- an agent invoked with a capability hint + output schema returns a validated
  typed object (against MockProvider, reusing the Phase-0 test_mock_provider pattern);
- a malformed structured response triggers a structural retry then AgentFailure
  on exhaustion (ADR 0018);
- cost is tracked per model through the Phase-0 cost ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel

from cyberlab_gen.agents import DEFAULT_STRUCTURAL_RETRY_ATTEMPTS, AgentRunner
from cyberlab_gen.errors import (
    AgentFailure,
    CapabilityUnreachable,
    EmitTruncated,
    MalformedOutput,
)
from cyberlab_gen.providers import (
    AgentLabel,
    CallOutcome,
    CapabilityHint,
    CostLedger,
    CostLedgerEntry,
    Message,
    MessageRole,
    MockProvider,
    ModelRankings,
    Provider,
    ProviderRegistry,
    ProviderResponse,
    RankingEntry,
    TokenUsage,
    ToolDefinition,
    ToolExecutor,
)


class _Out(BaseModel):
    value: str


def _rankings() -> ModelRankings:
    return ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "openai", "model": "model-top"},
                    {"provider": "anthropic", "model": "model-second"},
                ],
                CapabilityHint.LONG_CONTEXT_EXTRACTION.value: [
                    {"provider": "anthropic", "model": "model-second"},
                ],
            }
        }
    )


def _runner(
    provider: Provider,
    *,
    configured: frozenset[str],
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
) -> AgentRunner:
    registry = ProviderRegistry(_rankings(), configured)
    return AgentRunner(
        agent_label=AgentLabel.EXTRACTOR,
        agent_dir="extractor",
        provider=provider,
        registry=registry,
        structural_retry_attempts=structural_retry_attempts,
    )


# --- resolution ----------------------------------------------------------------


def test_resolution_skips_unreachable_and_picks_highest_ranked_reachable() -> None:
    # openai is top-ranked but NOT configured; anthropic is configured -> it wins.
    runner = _runner(MockProvider(), configured=frozenset({"anthropic"}))
    provider_name, model = runner.resolve_model(CapabilityHint.HIGH_QUALITY_REASONING)
    assert provider_name == "anthropic"
    assert model == "model-second"


def test_resolution_prefers_top_ranked_when_reachable() -> None:
    runner = _runner(MockProvider(), configured=frozenset({"anthropic", "openai"}))
    provider_name, model = runner.resolve_model(CapabilityHint.HIGH_QUALITY_REASONING)
    assert provider_name == "openai"
    assert model == "model-top"


def test_resolution_raises_when_no_provider_reachable() -> None:
    # Construction-time coverage validation fires for the unreachable capability.
    with pytest.raises(CapabilityUnreachable):
        _runner(MockProvider(), configured=frozenset())


def test_no_concrete_model_name_in_call_surface_source() -> None:
    # Guards the "no agent-facing code references a concrete model name" criterion.
    import inspect

    from cyberlab_gen.agents import call_surface, prompts

    for module in (call_surface, prompts):
        src = inspect.getsource(module).lower()
        for needle in ("claude-", "gpt-", "opus", "sonnet", "haiku"):
            assert needle not in src, f"{module.__name__} mentions a model name: {needle}"


# --- happy path ----------------------------------------------------------------


async def test_run_returns_validated_typed_object() -> None:
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        agent_label=AgentLabel.EXTRACTOR,
        response=_Out(value="extracted"),
    )
    runner = _runner(provider, configured=frozenset({"anthropic"}))
    msgs = [Message(role=MessageRole.USER, content="blog text")]
    resp = await runner.run(
        msgs,
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
    )
    assert isinstance(resp.output, _Out)
    assert resp.output.value == "extracted"


async def test_build_messages_uses_seeded_base_prompt() -> None:
    runner = _runner(MockProvider(), configured=frozenset({"anthropic"}))
    msgs = runner.build_messages(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        user_content="hello",
    )
    assert msgs[0].role is MessageRole.SYSTEM
    assert "Extractor" in msgs[0].content
    assert msgs[1].role is MessageRole.USER
    assert msgs[1].content == "hello"


# --- structural retry / agent-failure path -------------------------------------


class _FailingProvider(Provider):
    """Raises MalformedOutput for the first ``fail_times`` calls, then succeeds.

    MockProvider returns its registered response deterministically and never
    retries, so it cannot exercise the structural-retry path. This double does
    (ADR 0018).
    """

    def __init__(self, *, fail_times: int, success: _Out) -> None:
        self._fail_times = fail_times
        self._success = success
        self.calls = 0

    @property
    def name(self) -> str:
        return "failing"

    async def _attempt[T: BaseModel](self, output_schema: type[T]) -> ProviderResponse[T]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise MalformedOutput(f"synthetic malformation on call {self.calls}")
        success = output_schema.model_validate(self._success.model_dump())
        return ProviderResponse[T](
            output=success,
            raw_text=success.model_dump_json(),
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost_usd=Decimal("0.01")),
            model="some-model",
            provider="failing",
        )

    async def complete[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt(output_schema)

    async def complete_with_tools[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt(output_schema)


async def test_structural_retry_recovers_within_budget() -> None:
    # Fail once, succeed on the retry (budget = 2 retries -> 3 attempts).
    provider = _FailingProvider(fail_times=1, success=_Out(value="ok"))
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=2)
    resp = await runner.run(
        [Message(role=MessageRole.USER, content="x")],
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
    )
    assert resp.output.value == "ok"
    assert provider.calls == 2  # one failure + one success


async def test_structural_retry_exhaustion_raises_agent_failure() -> None:
    # Always malformed; budget = 2 retries -> 3 attempts -> AgentFailure.
    provider = _FailingProvider(fail_times=99, success=_Out(value="never"))
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=2)
    with pytest.raises(AgentFailure) as exc_info:
        await runner.run(
            [Message(role=MessageRole.USER, content="x")],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        )
    assert provider.calls == 3  # initial + 2 retries
    # The originating MalformedOutput is preserved as the cause (audit trail).
    cause: BaseException | None = exc_info.value.__cause__
    assert isinstance(cause, MalformedOutput)


class _IdenticalFailingProvider(Provider):
    """Always raises MalformedOutput with the SAME message (no progress).

    Models the production burn: the model reproduces the identical structural
    failure (``chain is required when in_scope``) on every attempt. The call
    surface must stop paying for retries the moment it sees the same failure
    twice in a row, rather than exhausting the whole budget (ADR 0032).
    """

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "identical-failing"

    async def _attempt(self) -> ProviderResponse[_Out]:
        self.calls += 1
        raise MalformedOutput("chain is required when in_scope")

    async def complete[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt()  # type: ignore[return-value]

    async def complete_with_tools[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt()  # type: ignore[return-value]


async def test_identical_structural_failure_aborts_early_without_exhausting_budget() -> None:
    # A repeating IDENTICAL structural failure is not worth paying to retry: the
    # call surface bails after the second identical failure even though the budget
    # (2 retries -> 3 attempts) would otherwise allow a third (ADR 0032).
    provider = _IdenticalFailingProvider()
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=2)
    with pytest.raises(AgentFailure):
        await runner.run(
            [Message(role=MessageRole.USER, content="x")],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        )
    assert provider.calls == 2  # bailed early; did NOT do the 3rd attempt


async def test_distinct_structural_failures_still_use_full_budget() -> None:
    # Progress (a DIFFERENT error each attempt) must NOT trigger the early bail —
    # the model may be converging, so the full budget is honoured (ADR 0032).
    provider = _FailingProvider(fail_times=99, success=_Out(value="never"))
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=2)
    with pytest.raises(AgentFailure):
        await runner.run(
            [Message(role=MessageRole.USER, content="x")],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        )
    assert provider.calls == 3  # distinct messages ("on call N") -> full budget


class _TruncatingProvider(Provider):
    """Always raises ``EmitTruncated`` (the emit was cut off at max_tokens).

    Models the truncation halt (ADR 0033): retrying regenerates the same oversized
    output and truncates again, so the call surface must re-raise it past the
    structural-retry budget rather than spend attempts on it.
    """

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "truncating"

    async def _attempt(self) -> ProviderResponse[_Out]:
        self.calls += 1
        raise EmitTruncated("the _Out emit was truncated at the 16384-token output limit")

    async def complete[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt()  # type: ignore[return-value]

    async def complete_with_tools[T: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T]:
        return await self._attempt()  # type: ignore[return-value]


async def test_truncation_halts_immediately_without_structural_retry() -> None:
    # A truncated emit (ADR 0033) is non-retryable: the call surface re-raises
    # EmitTruncated past the structural-retry budget on the FIRST attempt rather than
    # spending the budget (which would just truncate again). It surfaces as
    # EmitTruncated, NOT the generic AgentFailure — so the halt_reason names the real
    # remedy (raise max_tokens / shorten input), and a single call is made.
    provider = _TruncatingProvider()
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=2)
    with pytest.raises(EmitTruncated):
        await runner.run(
            [Message(role=MessageRole.USER, content="x")],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        )
    assert provider.calls == 1  # no structural retry; halted on the first truncation


async def test_zero_retry_budget_fails_on_first_malformation() -> None:
    provider = _FailingProvider(fail_times=99, success=_Out(value="never"))
    runner = _runner(provider, configured=frozenset({"anthropic"}), structural_retry_attempts=0)
    with pytest.raises(AgentFailure):
        await runner.run(
            [Message(role=MessageRole.USER, content="x")],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        )
    assert provider.calls == 1


def test_negative_retry_budget_rejected() -> None:
    with pytest.raises(ValueError, match="structural_retry_attempts"):
        _runner(MockProvider(), configured=frozenset({"anthropic"}), structural_retry_attempts=-1)


# --- cost tracking through the Phase-0 ledger ----------------------------------


async def test_cost_tracked_per_model_through_ledger() -> None:
    # The call surface returns usage on the ProviderResponse; the framework records
    # it into the Phase-0 CostLedger, which rolls up per model. This asserts the
    # per-model rollup end to end through the ledger contract.
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        agent_label=AgentLabel.EXTRACTOR,
        response=_Out(value="x"),
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost_usd=Decimal("0.25")),
    )
    runner = _runner(provider, configured=frozenset({"anthropic"}))
    provider_name, model = runner.resolve_model(CapabilityHint.LONG_CONTEXT_EXTRACTION)

    resp = await runner.run(
        [Message(role=MessageRole.USER, content="x")],
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
    )

    ledger = CostLedger(run_id="t", cap_usd=None)
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime.now(UTC),
            agent_label=runner.agent_label,
            provider=provider_name,
            model=model,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            usage=resp.usage,
            outcome=CallOutcome.SUCCESS,
            purpose="extraction call",
        )
    )
    assert ledger.total_usd == Decimal("0.25")
    assert ledger.by_model() == {model: Decimal("0.25")}
    assert ledger.by_agent() == {AgentLabel.EXTRACTOR: Decimal("0.25")}


# `RankingEntry` import kept for clarity of the ranking shape under test; ensure it
# is exercised so the import is not flagged as unused.
def test_ranking_entry_shape() -> None:
    entry = RankingEntry(provider="anthropic", model="model-second")
    assert (entry.provider, entry.model) == ("anthropic", "model-second")
