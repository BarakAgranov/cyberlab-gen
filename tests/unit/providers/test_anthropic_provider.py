"""Unit tests for the real ``AnthropicProvider`` call bodies (offline, fake client).

These exercise the loop/retry/cost logic against an injected fake vendor client —
no network. The *live* path (a real Anthropic API call) is covered by
``tests/integration/test_anthropic_provider_live.py`` against a recorded cassette;
per the task brief, green mocks here are necessary but NOT sufficient. The fake is
exactly the kind of thing that hid the Phase-0 stub, so it is paired with that
live regression test.

Architectural source: ``provider-interface.md`` §4/§6, ADR 0018, ADR 0027.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from cyberlab_gen.errors import HardFailure, MalformedOutput, ToolLoopError, TransientFailure
from cyberlab_gen.providers.anthropic_provider import (
    AnthropicProvider,
    _UsageAccumulator,  # pyright: ignore[reportPrivateUsage] -- the one test with no public path
)
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from cyberlab_gen.providers.cost_ledger import load_pricing_table
from cyberlab_gen.providers.retries import RetryStrategy

# Zero-delay strategies so retry tests don't actually sleep.
_NO_SLEEP_TRANSIENT = RetryStrategy(
    max_attempts=3, base_delay_seconds=0.0, backoff_factor=1.0, jitter_fraction=0.0
)
_TWO_ATTEMPT_MALFORMED = RetryStrategy(
    max_attempts=2, base_delay_seconds=0.0, backoff_factor=1.0, jitter_fraction=0.0
)

_HAIKU = "claude-haiku-4-5-20251001"


class Greeting(BaseModel):
    greeting: str
    audience: str


# The adapter names the forced structured-output tool ``emit_<ClassName>``
# (see ``anthropic_provider._emit_tool``); for ``Greeting`` that is ``emit_Greeting``.
_EMIT_NAME = "emit_Greeting"


# --- fake vendor client -----------------------------------------------------


class _Block:
    def __init__(
        self,
        block_type: str,
        *,
        text: str | None = None,
        id: str | None = None,
        name: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> None:
        self.type = block_type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


def _text(text: str) -> _Block:
    return _Block("text", text=text)


def _tool_use(call_id: str, name: str, args: dict[str, Any]) -> _Block:
    return _Block("tool_use", id=call_id, name=name, input=args)


class _Usage:
    def __init__(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _Response:
    def __init__(self, content: list[_Block], usage: _Usage, stop_reason: str = "end_turn") -> None:
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:  # noqa: ANN401 -- test fake mimics the untyped anthropic SDK messages.create()
        self.calls.append(kwargs)
        if not self._outcomes:
            raise AssertionError("fake client ran out of canned outcomes")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = _FakeMessages(outcomes)


def _provider(
    outcomes: list[Any], *, transient: RetryStrategy = _NO_SLEEP_TRANSIENT
) -> AnthropicProvider:
    return AnthropicProvider(
        client=_FakeClient(outcomes),
        transient_retries=transient,
        malformed_retries=_TWO_ATTEMPT_MALFORMED,
    )


def _messages() -> list[Message]:
    return [
        Message(role=MessageRole.SYSTEM, content="You are terse."),
        Message(role=MessageRole.USER, content="Greet the world."),
    ]


def _timeout() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def _status_error(code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(code, request=request)
    return anthropic.APIStatusError("boom", response=response, body=None)


class _Executor:
    def __init__(self, content: str) -> None:
        self.content = content
        self.seen: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.seen.append(call)
        return ToolResult(call_id=call.call_id, content=self.content)


# --- complete: happy path ---------------------------------------------------


async def test_complete_returns_parsed_structured_output() -> None:
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(100, 20),
            )
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.output == Greeting(greeting="hi", audience="world")
    assert resp.provider == "anthropic"
    assert resp.model == _HAIKU
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 20
    # Real cost, not a placeholder zero (the second "green but hollow" trap).
    assert resp.usage.cost_usd > Decimal("0")
    assert resp.conversation[-1].role is MessageRole.ASSISTANT
    assert resp.conversation[-1].content == resp.raw_text


async def test_complete_forces_the_emit_tool() -> None:
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(10, 5),
            )
        ]
    )
    await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": _EMIT_NAME}
    assert call["model"] == _HAIKU
    # System text is lifted out of the messages array per Anthropic's API.
    assert call["system"] == "You are terse."
    assert all(m["role"] != "system" for m in call["messages"])


# --- complete: malformed-output retry --------------------------------------


async def test_complete_retries_on_malformed_then_succeeds() -> None:
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi"})], _Usage(100, 20)
            ),  # missing audience
            _Response(
                [_tool_use("t2", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(50, 10),
            ),
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.output.audience == "world"
    # Usage accumulates across BOTH billed attempts (honest cost).
    assert resp.usage.input_tokens == 150
    assert resp.usage.output_tokens == 30
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 2


async def test_complete_raises_malformed_after_budget_exhausted() -> None:
    provider = _provider(
        [
            _Response([_tool_use("t1", _EMIT_NAME, {"greeting": "hi"})], _Usage(10, 5)),
            _Response([_tool_use("t2", _EMIT_NAME, {"nope": "bad"})], _Usage(10, 5)),
        ]
    )
    with pytest.raises(MalformedOutput):
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )


async def test_complete_treats_missing_emit_tool_as_malformed() -> None:
    provider = _provider(
        [
            _Response([_text("here you go")], _Usage(10, 5)),
            _Response([_text("still no tool call")], _Usage(10, 5)),
        ]
    )
    with pytest.raises(MalformedOutput):
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )


# --- transient + hard failures ---------------------------------------------


async def test_transient_failure_retries_then_succeeds() -> None:
    provider = _provider(
        [
            _timeout(),
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(10, 5),
            ),
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.output.greeting == "hi"
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 2


async def test_transient_failure_exhausted_raises_transient_failure() -> None:
    provider = _provider(
        [_timeout(), _timeout()],
        transient=RetryStrategy(
            max_attempts=2, base_delay_seconds=0.0, backoff_factor=1.0, jitter_fraction=0.0
        ),
    )
    with pytest.raises(TransientFailure):
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )


async def test_4xx_status_error_is_hard_failure_no_retry() -> None:
    provider = _provider([_status_error(400)])
    with pytest.raises(HardFailure):
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 1  # not retried


async def test_5xx_status_error_is_transient() -> None:
    provider = _provider(
        [
            _status_error(503),
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(10, 5),
            ),
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.output.greeting == "hi"


# --- complete_with_tools ----------------------------------------------------


def _lookup_tool() -> ToolDefinition:
    return ToolDefinition(
        name="external_lookup",
        description="Look something up.",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )


async def test_complete_with_tools_executes_tool_then_finishes() -> None:
    executor = _Executor(content="NVD says CVE-2024-0001 is critical")
    provider = _provider(
        [
            _Response(
                [_tool_use("c1", "external_lookup", {"q": "CVE-2024-0001"})],
                _Usage(100, 20),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("c2", _EMIT_NAME, {"greeting": "done", "audience": "analyst"})],
                _Usage(40, 10),
            ),
        ]
    )
    resp = await provider.complete_with_tools(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        tools=[_lookup_tool()],
        tool_executor=executor,
        agent_label=AgentLabel.EXTRACTOR,
        max_iterations=5,
    )
    assert resp.output == Greeting(greeting="done", audience="analyst")
    # The real tool call is surfaced (search-before-claim / Jury inspect this).
    assert resp.tool_calls == [
        ToolCall(call_id="c1", tool_name="external_lookup", arguments={"q": "CVE-2024-0001"})
    ]
    # The conversation trace carries the assistant tool-call turn AND the tool result.
    assert any(m.role is MessageRole.ASSISTANT and m.tool_calls for m in resp.conversation)
    tool_msgs = [m for m in resp.conversation if m.role is MessageRole.TOOL]
    assert tool_msgs and tool_msgs[0].content == "NVD says CVE-2024-0001 is critical"
    assert tool_msgs[0].tool_call_id == "c1"
    # Cost accumulates across both billed iterations.
    assert resp.usage.input_tokens == 140
    assert executor.seen[0].tool_name == "external_lookup"


async def test_complete_with_tools_raises_tool_loop_error_when_never_finishes() -> None:
    executor = _Executor(content="result")
    provider = _provider(
        [
            _Response(
                [_tool_use("c1", "external_lookup", {"q": "a"})],
                _Usage(10, 5),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("c2", "external_lookup", {"q": "b"})],
                _Usage(10, 5),
                stop_reason="tool_use",
            ),
        ]
    )
    with pytest.raises(ToolLoopError):
        await provider.complete_with_tools(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            tools=[_lookup_tool()],
            tool_executor=executor,
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=2,
        )


async def test_complete_with_tools_coerces_final_output_when_model_ends_with_text() -> None:
    executor = _Executor(content="result")
    provider = _provider(
        [
            # Model ends its turn with plain text, never calling the emit tool.
            _Response(
                [_text("All done, the greeting is hi.")], _Usage(30, 8), stop_reason="end_turn"
            ),
            # Forced-emit follow-up coerces the structured answer.
            _Response(
                [_tool_use("c9", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(20, 6),
            ),
        ]
    )
    resp = await provider.complete_with_tools(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        tools=[_lookup_tool()],
        tool_executor=executor,
        agent_label=AgentLabel.EXTRACTOR,
        max_iterations=5,
    )
    assert resp.output == Greeting(greeting="hi", audience="world")


# --- model resolution + cost surfacing -------------------------------------


@pytest.mark.parametrize(
    ("capability", "expected_model"),
    [
        (CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT, _HAIKU),
        (CapabilityHint.LONG_CONTEXT_EXTRACTION, "claude-opus-4-7"),
        (CapabilityHint.HIGH_QUALITY_REASONING, "claude-opus-4-7"),
    ],
)
async def test_capability_resolves_to_first_anthropic_model(
    capability: CapabilityHint, expected_model: str
) -> None:
    # Asserted through the public surface: the resolved model is what the response
    # reports (the adapter picks the first anthropic entry for the capability).
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(10, 5),
            )
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=capability,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.model == expected_model


async def test_complete_reads_cache_tokens_into_usage() -> None:
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(100, 20, cache_read_input_tokens=40, cache_creation_input_tokens=12),
            )
        ]
    )
    resp = await provider.complete(
        _messages(),
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert resp.usage.cache_read_tokens == 40
    assert resp.usage.cache_write_tokens == 12


def test_missing_pricing_entry_surfaces_as_hard_failure_not_zero_cost() -> None:
    # The one test that reaches the internal accumulator directly: there is no
    # public path to a resolved model absent from pricing.yaml (all ranked models
    # are priced), and "surface, don't silently zero" is a load-bearing guarantee.
    acc = _UsageAccumulator(input_tokens=100, output_tokens=20)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(HardFailure, match="No pricing entry"):
        acc.finalize(model="claude-model-not-in-pricing", pricing=load_pricing_table())
