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
from typing import Any, cast

import anthropic
import httpx
import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.errors import (
    EmitTruncated,
    HardFailure,
    MalformedOutput,
    ToolLoopError,
    TransientFailure,
)
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


def _block_field(block: object, name: str) -> Any:  # noqa: ANN401 -- reads a heterogeneous block field
    if isinstance(block, dict):
        return cast("dict[str, Any]", block).get(name)
    return getattr(block, name, None)


def _contract_400() -> anthropic.APIStatusError:
    """The exact 400 Anthropic raises when a tool_use has no following tool_result."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.APIStatusError(
        "messages.N: tool_use ids were found without tool_result blocks immediately after",
        response=response,
        body=None,
    )


class _ContractCheckingMessages(_FakeMessages):
    """A fake ``messages`` that enforces Anthropic's tool_use/tool_result contract.

    On every ``create`` it validates the ``messages`` array the adapter built: each
    assistant turn with N ``tool_use`` blocks must be *immediately* followed by a
    user message carrying a ``tool_result`` for every one of those ids, before any
    other content. A violation raises the real 400 — so a loop that drops or
    mis-orders a tool_result fails here exactly as it did in production.
    """

    async def create(self, **kwargs: Any) -> Any:  # noqa: ANN401 -- mirrors the SDK signature
        self._assert_contract(list(kwargs.get("messages", [])))
        return await super().create(**kwargs)

    @staticmethod
    def _assert_contract(messages: list[Any]) -> None:
        for i, msg in enumerate(messages):
            content = _block_field(msg, "content")
            if _block_field(msg, "role") != "assistant" or not isinstance(content, list):
                continue
            tool_use_ids = [
                _block_field(b, "id")
                for b in cast("list[Any]", content)
                if _block_field(b, "type") == "tool_use"
            ]
            if not tool_use_ids:
                continue
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            nxt_content = _block_field(nxt, "content") if nxt is not None else None
            if _block_field(nxt, "role") != "user" or not isinstance(nxt_content, list):
                raise _contract_400()
            result_ids: list[Any] = []
            seen_other = False
            for b in cast("list[Any]", nxt_content):
                if _block_field(b, "type") == "tool_result":
                    if seen_other:
                        raise _contract_400()  # tool_results must lead the message
                    result_ids.append(_block_field(b, "tool_use_id"))
                else:
                    seen_other = True
            if any(tid not in result_ids for tid in tool_use_ids):
                raise _contract_400()


class _ContractClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = _ContractCheckingMessages(outcomes)


def _contract_provider(outcomes: list[Any]) -> AnthropicProvider:
    return AnthropicProvider(
        client=_ContractClient(outcomes),
        transient_retries=_NO_SLEEP_TRANSIENT,
        malformed_retries=_TWO_ATTEMPT_MALFORMED,
    )


def _result_blocks(messages: list[Any]) -> list[dict[str, Any]]:
    """The tool_result blocks of the last user message that carries them."""
    for msg in reversed(messages):
        content = _block_field(msg, "content")
        if _block_field(msg, "role") == "user" and isinstance(content, list):
            blocks = [
                cast("dict[str, Any]", b)
                for b in cast("list[Any]", content)
                if _block_field(b, "type") == "tool_result"
            ]
            if blocks:
                return blocks
    raise AssertionError("no tool_result message found")


async def test_complete_with_tools_multi_tool_turn_answers_every_call_id_in_order() -> None:
    # The production failure: one assistant turn with multiple tool_use blocks
    # (here two real tools AND a premature emit) must be answered by a tool_result
    # for EVERY id, in order — else Anthropic 400s. Fails on the pre-fix loop,
    # which dropped the emit block's result.
    executor = _Executor(content="lookup-result")
    provider = _contract_provider(
        [
            _Response(
                [
                    _tool_use("c1", "external_lookup", {"q": "CVE-1"}),
                    _tool_use("c2", "external_lookup", {"q": "CVE-2"}),
                    _tool_use("e1", _EMIT_NAME, {"greeting": "premature", "audience": "x"}),
                ],
                _Usage(100, 20),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("c3", _EMIT_NAME, {"greeting": "done", "audience": "analyst"})],
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
    # Both real tools executed, in order; the emit block is not "executed".
    assert [c.call_id for c in executor.seen] == ["c1", "c2"]
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    result_ids = [b["tool_use_id"] for b in _result_blocks(fake.calls[1]["messages"])]
    assert result_ids == ["c1", "c2", "e1"]  # every call_id, in order, none dropped


async def test_complete_with_tools_multi_turn_sequence_then_emits() -> None:
    # Model calls a tool, gets a result, calls TWO more (parallel), then emits.
    executor = _Executor(content="r")
    provider = _contract_provider(
        [
            _Response(
                [_tool_use("c1", "external_lookup", {"q": "a"})],
                _Usage(10, 5),
                stop_reason="tool_use",
            ),
            _Response(
                [
                    _tool_use("c2", "external_lookup", {"q": "b"}),
                    _tool_use("c3", "propose_facet", {"name": "target:aws"}),
                ],
                _Usage(10, 5),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("e", _EMIT_NAME, {"greeting": "ok", "audience": "a"})],
                _Usage(10, 5),
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
    assert resp.output == Greeting(greeting="ok", audience="a")
    # Every real tool call across both turns ran and is surfaced, in order.
    assert [c.call_id for c in executor.seen] == ["c1", "c2", "c3"]
    assert [c.call_id for c in resp.tool_calls] == ["c1", "c2", "c3"]


class _PartialFailExecutor:
    """Executes normally except for ``fail_id``, where ``execute`` RAISES."""

    def __init__(self, fail_id: str) -> None:
        self._fail_id = fail_id
        self.seen: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.seen.append(call)
        if call.call_id == self._fail_id:
            raise RuntimeError("nvd exploded")
        return ToolResult(call_id=call.call_id, content="ok")


async def test_complete_with_tools_errored_tool_still_yields_is_error_result() -> None:
    # One of two parallel tool calls raises during execution. Its tool_result must
    # still be present (with is_error), never dropped — else the next turn 400s.
    # The pre-fix loop let the executor exception abort the whole call.
    executor = _PartialFailExecutor(fail_id="c2")
    provider = _contract_provider(
        [
            _Response(
                [
                    _tool_use("c1", "external_lookup", {"q": "a"}),
                    _tool_use("c2", "external_lookup", {"q": "b"}),
                ],
                _Usage(10, 5),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("e", _EMIT_NAME, {"greeting": "done", "audience": "a"})],
                _Usage(10, 5),
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
    assert resp.output == Greeting(greeting="done", audience="a")
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    results = {b["tool_use_id"]: b for b in _result_blocks(fake.calls[1]["messages"])}
    assert set(results) == {"c1", "c2"}  # both answered, neither dropped
    assert results["c2"]["is_error"] is True
    assert "nvd exploded" in results["c2"]["content"]
    assert results["c1"]["is_error"] is False


async def test_complete_with_tools_invalid_emit_args_never_sends_dangling_emit() -> None:
    # THE real bug (ADR 0031): the model finishes by calling emit, but its args fail
    # schema validation. The adapter must ANSWER that emit tool_use with a
    # tool_result before the forced-retry's next API call — never submit the dangling
    # emit turn. The old loop appended the emit turn and handed it to the forced
    # extract UNANSWERED, so its first request carried a trailing unanswered tool_use
    # (the dumped "[7] assistant tool_use=[id_B]" with no [8]) → the 400.
    executor = _Executor(content="unused")
    provider = _contract_provider(
        [
            # finish-turn: emit with MISSING 'audience' → fails Greeting validation.
            _Response([_tool_use("e1", _EMIT_NAME, {"greeting": "hi"})], _Usage(100, 20)),
            # forced-retry turn: emit with corrected args.
            _Response(
                [_tool_use("e2", _EMIT_NAME, {"greeting": "done", "audience": "world"})],
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
    assert resp.output == Greeting(greeting="done", audience="world")
    # the forced-retry request ANSWERED the invalid emit's tool_use before re-asking.
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    result_ids = [b["tool_use_id"] for b in _result_blocks(fake.calls[1]["messages"])]
    assert result_ids == ["e1"]


async def test_complete_with_tools_max_iterations_raises_without_a_malformed_call() -> None:
    # max_iterations is hit with the model still emitting tool calls. The adapter
    # must raise ToolLoopError WITHOUT sending one more request carrying a dangling
    # tool_use. With the contract-checking fake a dangling request surfaces as
    # HardFailure, so getting ToolLoopError (not HardFailure) proves no malformed
    # API call was attempted.
    executor = _Executor(content="r")
    provider = _contract_provider(
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


def test_debug_summarize_messages_flags_the_unanswered_tool_use() -> None:
    # The instrumentation must point at the malformed message: an assistant turn
    # whose tool_use id is not answered in the next message is flagged MALFORMED,
    # and a balanced turn is not.
    from cyberlab_gen.providers.anthropic_provider import (
        _debug_summarize_messages,  # pyright: ignore[reportPrivateUsage]
    )

    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                _tool_use("c1", "external_lookup", {"q": "a"}),
                _tool_use("c2", _EMIT_NAME, {"greeting": "x", "audience": "y"}),
            ],
        },
        # only c1 answered — c2 (the emit) is orphaned, reproducing the real 400.
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "r"}]},
    ]
    summary = _debug_summarize_messages(messages)
    assert "[1] role=assistant" in summary
    assert "MALFORMED" in summary
    assert "'c2'" in summary  # names the orphaned id
    # a fully-answered version is not flagged.
    messages[2]["content"] = [  # type: ignore[index]
        {"type": "tool_result", "tool_use_id": "c1", "content": "r"},
        {"type": "tool_result", "tool_use_id": "c2", "content": "r"},
    ]
    assert "MALFORMED" not in _debug_summarize_messages(messages)


async def test_complete_with_tools_bails_early_on_repeated_identical_emit_error() -> None:
    # The production burn (ADR 0032): the model finishes by emitting an AttackSpec
    # that fails the SAME validation every time. After the finish-turn emit fails,
    # the forced-extract reproduces the IDENTICAL error -> the provider must stop
    # rather than spend the remaining malformed-retry attempt. With only TWO canned
    # outcomes, a non-bailing loop would demand a third and the fake would raise
    # "ran out of canned outcomes"; bailing raises MalformedOutput after 2 calls.
    executor = _Executor(content="unused")
    provider = _provider(
        [
            # finish-turn emit: missing 'audience' -> identical validation error.
            _Response([_tool_use("e1", _EMIT_NAME, {"greeting": "hi"})], _Usage(100, 20)),
            # forced-extract attempt 1: SAME invalid args -> SAME error -> bail.
            _Response([_tool_use("e2", _EMIT_NAME, {"greeting": "hi"})], _Usage(40, 10)),
        ]
    )
    with pytest.raises(MalformedOutput):
        await provider.complete_with_tools(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            tools=[_lookup_tool()],
            tool_executor=executor,
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=5,
        )
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 2  # finish-turn emit + ONE forced attempt, then bail


async def test_complete_with_tools_recovers_when_forced_extract_makes_progress() -> None:
    # The bail must only fire on an IDENTICAL repeat: if the forced-extract returns
    # corrected args, the call succeeds (no regression of the ADR 0031 fallback).
    executor = _Executor(content="unused")
    provider = _provider(
        [
            _Response([_tool_use("e1", _EMIT_NAME, {"greeting": "hi"})], _Usage(100, 20)),
            _Response(
                [_tool_use("e2", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
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
    assert resp.output == Greeting(greeting="hi", audience="world")


def _greeting_validation_error() -> PydanticValidationError:
    try:
        Greeting.model_validate({"greeting": "hi"})  # missing 'audience'
    except PydanticValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")  # pragma: no cover - defensive


def test_emit_diagnostic_verdict_distinguishes_truncation_from_content(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Symptom-2 (ADR 0032): the ALWAYS-ON verdict must name the load-bearing
    # distinction without any env var — was the emit TRUNCATED (stopped at
    # max_tokens) or COMPLETE-but-schema-invalid (a content problem)?
    import logging

    from cyberlab_gen.providers.anthropic_provider import (
        _DEBUG_EMIT_ENV,  # pyright: ignore[reportPrivateUsage]
        _dump_emit_on_validation_error,  # pyright: ignore[reportPrivateUsage]
    )

    monkeypatch.delenv(_DEBUG_EMIT_ENV, raising=False)  # verdict needs no env var
    exc = _greeting_validation_error()

    # stop_reason=max_tokens -> TRUNCATED verdict.
    with caplog.at_level(logging.WARNING, logger="cyberlab_gen.providers.anthropic_provider"):
        _dump_emit_on_validation_error(
            {"greeting": "hi"},
            schema_name="Greeting",
            exc=exc,
            stop_reason="max_tokens",
            output_tokens=4096,
            max_tokens=4096,
        )
    assert "TRUNCATED" in caplog.text

    caplog.clear()
    # stop_reason=end_turn, room to spare -> COMPLETE content-problem verdict.
    with caplog.at_level(logging.WARNING, logger="cyberlab_gen.providers.anthropic_provider"):
        _dump_emit_on_validation_error(
            {"greeting": "hi"},
            schema_name="Greeting",
            exc=exc,
            stop_reason="end_turn",
            output_tokens=512,
            max_tokens=4096,
        )
    assert "COMPLETE" in caplog.text
    assert "TRUNCATED" not in caplog.text


def test_emit_diagnostic_content_dump_is_opt_in(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The full emitted-content dump (large) is gated behind CYBERLAB_GEN_DEBUG_EMIT;
    # the chainless AttackSpec only appears on stderr when the var is set.
    from cyberlab_gen.providers.anthropic_provider import (
        _DEBUG_EMIT_ENV,  # pyright: ignore[reportPrivateUsage]
        _dump_emit_on_validation_error,  # pyright: ignore[reportPrivateUsage]
    )

    exc = _greeting_validation_error()
    banner = "=== EMIT VALIDATION FAILURE"

    monkeypatch.delenv(_DEBUG_EMIT_ENV, raising=False)
    _dump_emit_on_validation_error(
        {"greeting": "hi"}, schema_name="Greeting", exc=exc, stop_reason="end_turn"
    )
    assert banner not in capsys.readouterr().err  # no content dump without the var

    monkeypatch.setenv(_DEBUG_EMIT_ENV, "1")
    _dump_emit_on_validation_error(
        {"greeting": "hi"}, schema_name="Greeting", exc=exc, stop_reason="end_turn"
    )
    err = capsys.readouterr().err
    assert banner in err
    assert "greeting" in err  # the emitted argument is shown verbatim


async def test_complete_with_tools_surfaces_truncation_verdict_from_stop_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Wiring: a finish-turn emit that fails validation AND carries
    # stop_reason="max_tokens" must surface the TRUNCATED verdict through the real
    # call path (the response's stop_reason flows into the diagnostic) AND now HALT
    # with EmitTruncated rather than fall back to a doomed forced-extract regeneration
    # (ADR 0033). The diagnostic verdict still logs (the dump runs before the halt).
    import logging

    executor = _Executor(content="unused")
    provider = _provider(
        [
            _Response(
                [_tool_use("e1", _EMIT_NAME, {"greeting": "hi"})],  # missing audience
                _Usage(100, 4096),
                stop_reason="max_tokens",
            ),
        ]
    )
    with (
        caplog.at_level(logging.WARNING, logger="cyberlab_gen.providers.anthropic_provider"),
        pytest.raises(EmitTruncated),
    ):
        await provider.complete_with_tools(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            tools=[_lookup_tool()],
            tool_executor=executor,
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=5,
        )
    assert "TRUNCATED" in caplog.text  # the verdict reached the log, no env var


async def test_complete_with_tools_truncated_finish_emit_halts_without_retry() -> None:
    # P1 (ADR 0033): the finish-turn emit fails validation AND stopped at max_tokens
    # -> it was truncated mid-emit, not deliberately malformed. The provider must
    # HALT with EmitTruncated immediately instead of falling back to the forced-extract
    # regeneration (which would truncate again at the same budget and burn ~16K output
    # tokens). A second, *valid* outcome is canned: if the provider wrongly retried it
    # would be consumed and the call would succeed; the EmitTruncated + single-call
    # assertion proves the doomed regeneration never happens.
    executor = _Executor(content="unused")
    provider = _provider(
        [
            _Response(
                [_tool_use("e1", _EMIT_NAME, {"greeting": "hi"})],  # missing audience
                _Usage(1000, 4096),
                stop_reason="max_tokens",
            ),
            _Response(
                [_tool_use("e2", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(40, 10),
            ),
        ]
    )
    with pytest.raises(EmitTruncated) as exc_info:
        await provider.complete_with_tools(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            tools=[_lookup_tool()],
            tool_executor=executor,
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=5,
        )
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 1  # halted on the first truncated emit; no regeneration
    # The halt names the remedy so a run's halt_reason is actionable.
    msg = str(exc_info.value)
    assert "truncated" in msg
    assert "raise max_tokens" in msg
    # EmitTruncated is a MalformedOutput subtype (it IS a malformed parse) but a
    # distinct, non-retryable kind.
    assert isinstance(exc_info.value, MalformedOutput)


async def test_forced_extract_truncated_emit_halts_without_spending_budget() -> None:
    # The same halt via the no-tools forced-extract path: the first forced emit
    # truncates (stop_reason=max_tokens), so the malformed-retry loop must bail
    # immediately rather than spend its second attempt on a doomed regeneration.
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi"})],
                _Usage(500, 4096),
                stop_reason="max_tokens",
            ),
            _Response(
                [_tool_use("t2", _EMIT_NAME, {"greeting": "hi", "audience": "world"})],
                _Usage(40, 10),
            ),
        ]
    )
    with pytest.raises(EmitTruncated):
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )
    fake: _FakeMessages = provider._client.messages  # type: ignore[union-attr]
    assert len(fake.calls) == 1  # bailed on the first truncated attempt


async def test_truncation_halt_attaches_billed_usage_for_the_ledger() -> None:
    # Accounting fix (ADR 0033): the truncated attempt WAS billed; the raised
    # EmitTruncated carries the accumulated, costed usage + resolved model so the
    # cost-recording layer bills it even though no ProviderResponse comes back.
    provider = _provider(
        [
            _Response(
                [_tool_use("t1", _EMIT_NAME, {"greeting": "hi"})],
                _Usage(1000, 4096),
                stop_reason="max_tokens",
            )
        ]
    )
    with pytest.raises(EmitTruncated) as exc_info:
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )
    exc = exc_info.value
    assert exc.usage is not None
    assert exc.usage.input_tokens == 1000
    assert exc.usage.output_tokens == 4096
    assert exc.usage.cost_usd > Decimal("0")  # real billed cost, not a placeholder zero
    assert exc.model == _HAIKU


async def test_malformed_exhaustion_attaches_billed_usage_for_the_ledger() -> None:
    # A complete-but-invalid emit (NOT truncated) that exhausts the malformed budget
    # was billed on every attempt. The raised MalformedOutput must carry that summed
    # usage so the billed-but-raised spend is recorded (the accounting bug ADR 0033
    # fixes), not silently dropped.
    provider = _provider(
        [
            _Response([_tool_use("t1", _EMIT_NAME, {"greeting": "hi"})], _Usage(10, 5)),
            _Response([_tool_use("t2", _EMIT_NAME, {"nope": "bad"})], _Usage(10, 5)),
        ]
    )
    with pytest.raises(MalformedOutput) as exc_info:
        await provider.complete(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.EXTRACTOR,
        )
    exc = exc_info.value
    assert not isinstance(exc, EmitTruncated)  # a content problem, not truncation
    assert exc.usage is not None
    assert exc.usage.input_tokens == 20  # summed across BOTH billed attempts
    assert exc.model == _HAIKU


async def test_tool_loop_error_attaches_billed_usage_for_the_ledger() -> None:
    # A tool loop that never converges was billed for every iteration; the raised
    # ToolLoopError carries that summed usage so the wasted spend is recorded.
    executor = _Executor(content="result")
    provider = _provider(
        [
            _Response(
                [_tool_use("c1", "external_lookup", {"q": "a"})],
                _Usage(100, 20),
                stop_reason="tool_use",
            ),
            _Response(
                [_tool_use("c2", "external_lookup", {"q": "b"})],
                _Usage(100, 20),
                stop_reason="tool_use",
            ),
        ]
    )
    with pytest.raises(ToolLoopError) as exc_info:
        await provider.complete_with_tools(
            _messages(),
            output_schema=Greeting,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            tools=[_lookup_tool()],
            tool_executor=executor,
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=2,
        )
    exc = exc_info.value
    assert exc.usage is not None
    assert exc.usage.input_tokens == 200  # both billed iterations
    assert exc.model == _HAIKU


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
        (CapabilityHint.LONG_CONTEXT_EXTRACTION, "claude-opus-4-8"),
        (CapabilityHint.HIGH_QUALITY_REASONING, "claude-opus-4-8"),
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
