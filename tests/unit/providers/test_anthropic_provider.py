"""Unit tests for the pydantic-ai-backed ``AnthropicProvider`` (offline, no API).

The adapter wraps a pydantic-ai ``Agent`` (ADR 0036). These tests drive it with an
injected ``FunctionModel`` — pydantic-ai's offline test model — so the full agent
runtime (forced-tool structured output, the tool loop, output-retry budget, usage
accounting, truncation, exception mapping) is exercised with zero network and zero
API spend. The *live* path (a real Anthropic call) is covered by
``tests/integration/test_anthropic_provider_live.py``.

These assert the observable ``Provider`` contract and the project-specific contracts
the migration must preserve: ``EmitTruncated`` on a length-truncated emit, billed
``usage``/``model`` attached to a raised ``ProviderError`` (ADR 0033), ``ToolLoopError``
on loop overflow, Decimal cost from our ``pricing.yaml``, and capability→model
resolution.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from cyberlab_gen.errors import (
    EmitTruncated,
    HardFailure,
    MalformedOutput,
    ToolLoopError,
    TransientFailure,
)
from cyberlab_gen.providers.anthropic_provider import (
    OUTPUT_TOOL_NAME,
    AnthropicProvider,
    output_forcing_model_settings,
)
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.tools import RunContext

_HAIKU = "claude-haiku-4-5-20251001"
_OPUS = "claude-opus-4-8"
_USAGE = RequestUsage(input_tokens=100, output_tokens=50)


class Greeting(BaseModel):
    greeting: str
    audience: str


# --- helpers ---------------------------------------------------------------


def _messages() -> list[Message]:
    return [
        Message(role=MessageRole.SYSTEM, content="You are terse."),
        Message(role=MessageRole.USER, content="Greet the world."),
    ]


def _provider(fn: object, **kwargs: object) -> AnthropicProvider:
    return AnthropicProvider(model=FunctionModel(fn), **kwargs)  # type: ignore[arg-type]


def _output_tool(info: AgentInfo) -> str:
    assert info.output_tools, "expected a forced output tool"
    return info.output_tools[0].name


async def _complete(
    provider: AnthropicProvider,
    *,
    capability: CapabilityHint = CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
    model: str = _HAIKU,
    max_tokens: int | None = None,
) -> ProviderResponse[Greeting]:
    # ``model`` is the registry-resolved id the call surface passes down (ADR 0071); it defaults to
    # the model the default capability resolves to, so existing model assertions are unchanged.
    return await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=capability,
        model=model,
        agent_label=AgentLabel.EXTRACTOR,
        max_tokens=max_tokens,
    )


def _emit_ok(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(tool_name=_output_tool(info), args={"greeting": "hi", "audience": "world"})
        ],
        usage=_USAGE,
    )


# --- happy path + provenance ----------------------------------------------


async def test_complete_returns_parsed_structured_output() -> None:
    resp = await _complete(_provider(_emit_ok))
    assert isinstance(resp.output, Greeting)
    assert resp.output.greeting == "hi"
    assert resp.output.audience == "world"
    assert resp.provider == "anthropic"


async def test_complete_reports_real_nonzero_cost() -> None:
    resp = await _complete(_provider(_emit_ok))
    # Cost comes from our pricing.yaml via compute_cost, not the placeholder zero.
    assert resp.usage.cost_usd > Decimal("0")
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 50


async def test_conversation_ends_with_assistant_raw_text() -> None:
    resp = await _complete(_provider(_emit_ok))
    assert resp.conversation[-1].role is MessageRole.ASSISTANT
    assert resp.conversation[-1].content == resp.raw_text


def test_name_is_anthropic() -> None:
    assert AnthropicProvider().name == "anthropic"


@pytest.mark.parametrize("model", [_HAIKU, _OPUS])
async def test_complete_uses_the_passed_model(model: str) -> None:
    """The adapter prices + reports the model it is *given*, not one it re-resolves (ADR 0071)."""
    resp = await _complete(_provider(_emit_ok), model=model)
    assert resp.model == model


async def test_complete_uses_passed_model_over_the_capability_default() -> None:
    """The capability no longer picks the model: passing _OPUS with the FAST_CHEAP capability (which
    the old adapter would have re-resolved to _HAIKU) reports _OPUS. This is the divergence ADR 0071
    closes — the adapter honours the registry-resolved id, never its own anthropic-first walk.
    """
    resp = await _complete(
        _provider(_emit_ok),
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        model=_OPUS,
    )
    assert resp.model == _OPUS


async def test_complete_reads_cache_tokens_into_usage() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=_output_tool(info), args={"greeting": "h", "audience": "w"})
            ],
            usage=RequestUsage(
                input_tokens=100, output_tokens=50, cache_read_tokens=20, cache_write_tokens=10
            ),
        )

    resp = await _complete(_provider(fn))
    assert resp.usage.cache_read_tokens == 20
    assert resp.usage.cache_write_tokens == 10


async def test_static_prefix_caching_is_enabled_in_model_settings() -> None:
    # B-i / ADR 0059: prompt caching is wired on the static prefix so repeated requests
    # within a run read the schema-heavy system prompt + tool defs + blog from cache instead
    # of re-billing the full ~41K base (investigation 0002 §3). With an injected model the
    # flags are inert; asserting they are SET is the deterministic check — the live
    # AnthropicModel applies the cache_control headers, and cache-token cost is pinned by
    # test_cost_ledger::test_compute_cost_with_cache_read_and_write.
    seen: dict[str, object] = {}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update(info.model_settings or {})
        return _emit_ok(messages, info)

    await _complete(_provider(fn))
    assert seen.get("anthropic_cache_instructions") is True
    assert seen.get("anthropic_cache_tool_definitions") is True
    assert seen.get("anthropic_cache_messages") is True
    assert seen.get("max_tokens") == 4096  # the existing output cap is still carried


async def test_cache_reads_surface_on_requests_after_the_first() -> None:
    # A multi-request _invoke: the first request writes cache, a later one reads it. The usage
    # plumbing must surface the cache tokens the provider reports — the win caching unlocks
    # (these were always zero while caching was unwired; investigation 0002 §1/§3).
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        name = _output_tool(info)
        if calls["n"] == 1:  # first request writes the cache, then a malformed re-prompt
            return ModelResponse(
                parts=[ToolCallPart(tool_name=name, args={"nope": 1})],
                usage=RequestUsage(input_tokens=100, output_tokens=50, cache_write_tokens=80),
            )
        return ModelResponse(  # second request reads the cached prefix
            parts=[ToolCallPart(tool_name=name, args={"greeting": "h", "audience": "w"})],
            usage=RequestUsage(input_tokens=20, output_tokens=50, cache_read_tokens=80),
        )

    resp = await _complete(_provider(fn, output_retries=1))
    assert resp.usage.cache_read_tokens == 80  # surfaced from the 2nd request
    assert resp.usage.cache_write_tokens == 80  # from the 1st


async def test_max_tokens_is_passed_through_to_the_model() -> None:
    seen: dict[str, object] = {}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["max_tokens"] = (info.model_settings or {}).get("max_tokens")
        return _emit_ok(messages, info)

    await _complete(_provider(fn), max_tokens=16384)
    assert seen["max_tokens"] == 16384


# --- malformed output ------------------------------------------------------


async def test_malformed_then_valid_recovers_within_output_budget() -> None:
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        name = _output_tool(info)
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=name, args={"nope": 1})], usage=_USAGE
            )
        return ModelResponse(
            parts=[ToolCallPart(tool_name=name, args={"greeting": "h", "audience": "w"})],
            usage=_USAGE,
        )

    resp = await _complete(_provider(fn, output_retries=1))
    assert isinstance(resp.output, Greeting)
    assert calls["n"] == 2  # the malformed re-prompt happened, then succeeded


async def test_complete_raises_malformed_after_output_budget_exhausted() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=_output_tool(info), args={"nope": 1})], usage=_USAGE
        )

    with pytest.raises(MalformedOutput) as exc_info:
        await _complete(_provider(fn, output_retries=1))
    # Billed-on-raise: spend accumulated across attempts is attached (ADR 0033).
    assert exc_info.value.usage is not None
    assert exc_info.value.model == _HAIKU


# --- truncation (ADR 0033) -------------------------------------------------


async def test_length_finish_reason_raises_emit_truncated_with_usage() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Valid args but stopped at the token limit: pydantic-ai would return this
        # silently; the adapter must halt it (ADR 0033).
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=_output_tool(info), args={"greeting": "h", "audience": "w"})
            ],
            usage=_USAGE,
            finish_reason="length",
        )

    with pytest.raises(EmitTruncated) as exc_info:
        await _complete(_provider(fn), max_tokens=4096)
    msg = str(exc_info.value).lower()
    assert "truncat" in msg
    assert "max_tokens" in msg
    assert exc_info.value.usage is not None
    assert exc_info.value.usage.cost_usd > Decimal("0")
    assert exc_info.value.model == _HAIKU


async def test_incomplete_tool_call_truncation_raises_emit_truncated() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Partial/unparseable args + length stop => pydantic-ai IncompleteToolCall.
        return ModelResponse(
            parts=[ToolCallPart(tool_name=_output_tool(info), args='{"greeting": "partial')],
            usage=_USAGE,
            finish_reason="length",
        )

    with pytest.raises(EmitTruncated) as exc_info:
        await _complete(_provider(fn))
    assert exc_info.value.usage is not None
    assert exc_info.value.model == _HAIKU


async def test_emit_truncated_message_distinguishes_per_request_limit_from_aggregate() -> None:
    # The cost.yaml output figure is a RunUsage AGGREGATE summed across every model-request
    # in one _invoke; the final emit still hit the per-request max_tokens. The halt message
    # must state both so an operator doesn't misread aggregate > limit as a second/wrong
    # ceiling (investigation 0002 §2, fix C). Here: malformed attempt 1 forces an
    # output-retry, attempt 2 is valid-but-length-truncated => 2 requests, output 50x2=100.
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        name = _output_tool(info)
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=name, args={"nope": 1})], usage=_USAGE
            )
        return ModelResponse(
            parts=[ToolCallPart(tool_name=name, args={"greeting": "h", "audience": "w"})],
            usage=_USAGE,
            finish_reason="length",
        )

    with pytest.raises(EmitTruncated) as exc_info:
        await _complete(_provider(fn, output_retries=1), max_tokens=4096)
    msg = str(exc_info.value).lower()
    assert "per-request" in msg  # the limit is per request...
    assert "max_tokens=4096" in msg
    assert "aggregat" in msg  # ...the call total is an aggregate...
    assert "2 model-request" in msg  # ...across this many requests
    assert "output_tokens=100" in msg
    assert exc_info.value.usage is not None
    assert exc_info.value.usage.output_tokens == 100


# --- tool loop -------------------------------------------------------------


class _EchoExecutor:
    """A ToolExecutor that echoes its query (the ToolExecutor protocol)."""

    def __init__(self, *, error: bool = False) -> None:
        self._error = error
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self._error:
            return ToolResult(call_id=call.call_id, content="lookup failed", is_error=True)
        return ToolResult(call_id=call.call_id, content=f"result:{call.arguments.get('q', '')}")


def _lookup_tool() -> ToolDefinition:
    return ToolDefinition(
        name="lookup",
        description="look something up",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    )


async def _complete_with_tools(
    provider: AnthropicProvider,
    executor: _EchoExecutor,
    *,
    model: str = _OPUS,
    max_iterations: int = 5,
) -> ProviderResponse[Greeting]:
    return await provider.complete_with_tools(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        model=model,
        tools=[_lookup_tool()],
        tool_executor=executor,
        agent_label=AgentLabel.EXTRACTOR,
        max_iterations=max_iterations,
    )


async def test_tool_loop_executes_tool_then_emits() -> None:
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="lookup", args={"q": "cve"}, tool_call_id="c1")],
                usage=_USAGE,
            )
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=_output_tool(info), args={"greeting": "h", "audience": "w"})
            ],
            usage=_USAGE,
        )

    executor = _EchoExecutor()
    resp = await _complete_with_tools(_provider(fn), executor)
    assert isinstance(resp.output, Greeting)
    assert [c.arguments.get("q") for c in executor.calls] == ["cve"]
    assert "lookup" in [c.tool_name for c in resp.tool_calls]
    assert any(m.role is MessageRole.TOOL for m in resp.conversation)


async def test_tool_executor_error_is_surfaced_then_recovers() -> None:
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="lookup", args={"q": "x"}, tool_call_id="c1")],
                usage=_USAGE,
            )
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=_output_tool(info), args={"greeting": "h", "audience": "w"})
            ],
            usage=_USAGE,
        )

    executor = _EchoExecutor(error=True)
    resp = await _complete_with_tools(_provider(fn), executor)
    # The error tool result became a ModelRetry; the model then emitted successfully.
    assert isinstance(resp.output, Greeting)
    assert len(executor.calls) == 1


async def test_tool_loop_error_when_never_finishes() -> None:
    # A model that defies even the forced tool_choice (ADR 0105) still fails safe on the budget: the
    # real Anthropic API enforces the force, but a FunctionModel ignores model_settings, so the loop
    # exhausts request_limit and raises ToolLoopError rather than hanging.
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="lookup", args={"q": "x"}, tool_call_id="c")],
            usage=_USAGE,
        )

    with pytest.raises(ToolLoopError) as exc_info:
        await _complete_with_tools(_provider(fn), _EchoExecutor(), max_iterations=2)
    assert exc_info.value.usage is not None
    assert exc_info.value.model == _OPUS


# --- forced terminal emit (ADR 0105) ---------------------------------------


def test_output_forcing_returns_base_unchanged_without_request_limit() -> None:
    base = AnthropicModelSettings(max_tokens=4096)
    assert output_forcing_model_settings(base, None) is base


def test_output_forcing_forces_output_tool_on_the_last_permitted_step() -> None:
    # ADR 0105: with request_limit=N the per-step callable forces tool_choice=[final_result] on the
    # last allowed request (run_step >= N), carrying the base settings through, so a tool loop emits
    # instead of overflowing the budget with zero output.
    from types import SimpleNamespace
    from typing import cast

    base = AnthropicModelSettings(max_tokens=4096)
    resolver = cast(
        "Callable[[RunContext[None]], AnthropicModelSettings]",
        output_forcing_model_settings(base, 3),
    )

    def at(step: int) -> AnthropicModelSettings:
        return resolver(cast("RunContext[None]", SimpleNamespace(run_step=step)))

    assert at(1).get("tool_choice") is None
    assert at(2).get("tool_choice") is None
    forced = at(3)
    assert forced.get("tool_choice") == [OUTPUT_TOOL_NAME]
    assert forced.get("max_tokens") == 4096  # base settings carried through


async def test_tool_loop_forces_output_on_last_step_then_emits() -> None:
    # End-to-end (ADR 0105): the provider wires the forcing callable, so on the last permitted request
    # tool_choice forces the output tool. A cooperative FunctionModel honours it (the real Anthropic
    # API enforces it) -> the loop emits instead of raising ToolLoopError.
    seen: list[object] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        choice = (info.model_settings or {}).get("tool_choice")
        seen.append(choice)
        name = _output_tool(info)
        if choice == [name]:  # forced -> emit the output tool
            return ModelResponse(
                parts=[ToolCallPart(tool_name=name, args={"greeting": "h", "audience": "w"})],
                usage=_USAGE,
            )
        return ModelResponse(  # otherwise keep looping on the regular tool
            parts=[ToolCallPart(tool_name="lookup", args={"q": "x"}, tool_call_id="c")],
            usage=_USAGE,
        )

    resp = await _complete_with_tools(_provider(fn), _EchoExecutor(), max_iterations=2)
    assert isinstance(resp.output, Greeting)  # emitted; did NOT raise ToolLoopError
    assert seen[-1] == [OUTPUT_TOOL_NAME]  # forced on the last permitted step
    assert seen[0] is None  # not forced on the first step


# --- transient / hard mapping ---------------------------------------------


async def test_429_maps_to_transient_failure() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(429, _HAIKU, body=None)

    with pytest.raises(TransientFailure):
        await _complete(_provider(fn))


async def test_5xx_maps_to_transient_failure() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(503, _HAIKU, body=None)

    with pytest.raises(TransientFailure):
        await _complete(_provider(fn))


async def test_4xx_maps_to_hard_failure() -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(400, _HAIKU, body=None)

    with pytest.raises(HardFailure):
        await _complete(_provider(fn))


# --- pricing safety --------------------------------------------------------


async def test_missing_pricing_entry_raises_hard_failure() -> None:
    # The passed model is what we price (ADR 0071): a model absent from the pricing table is a
    # HardFailure, no re-resolution involved.
    provider = _provider(_emit_ok)
    with pytest.raises(HardFailure) as exc_info:
        await _complete(provider, model="model-not-in-pricing")
    assert "pricing" in str(exc_info.value).lower()
