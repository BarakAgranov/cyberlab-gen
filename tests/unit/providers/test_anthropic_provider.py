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

import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from cyberlab_gen.errors import (
    EmitTruncated,
    HardFailure,
    MalformedOutput,
    ToolLoopError,
    TransientFailure,
)
from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
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
    max_tokens: int | None = None,
) -> ProviderResponse[Greeting]:
    return await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=capability,
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


@pytest.mark.parametrize(
    ("capability", "expected_model"),
    [
        (CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT, _HAIKU),
        (CapabilityHint.HIGH_QUALITY_REASONING, _OPUS),
        (CapabilityHint.LONG_CONTEXT_EXTRACTION, _OPUS),
    ],
)
async def test_capability_resolves_to_ranked_anthropic_model(
    capability: CapabilityHint, expected_model: str
) -> None:
    resp = await _complete(_provider(_emit_ok), capability=capability)
    assert resp.model == expected_model


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
    provider: AnthropicProvider, executor: _EchoExecutor, *, max_iterations: int = 5
) -> ProviderResponse[Greeting]:
    return await provider.complete_with_tools(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
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
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="lookup", args={"q": "x"}, tool_call_id="c")],
            usage=_USAGE,
        )

    with pytest.raises(ToolLoopError) as exc_info:
        await _complete_with_tools(_provider(fn), _EchoExecutor(), max_iterations=2)
    assert exc_info.value.usage is not None
    assert exc_info.value.model == _OPUS


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


async def test_missing_pricing_entry_raises_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(_emit_ok)

    def _bad_resolve(_capability: CapabilityHint) -> str:
        return "model-not-in-pricing"

    monkeypatch.setattr(provider, "_resolve_model", _bad_resolve)
    with pytest.raises(HardFailure) as exc_info:
        await _complete(provider)
    assert "pricing" in str(exc_info.value).lower()
