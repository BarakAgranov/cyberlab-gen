"""Mock provider for tests — canned responses, no real API calls.

Architectural source: ``provider-interface.md`` §7. This is the
load-bearing test fixture for the entire project: every agent's unit
tests register canned responses and assert on the agent's behavior
against them. Unmatched calls raise ``UnmatchedMockCall`` so test gaps
fail loudly rather than hanging or returning empty data.

Phase 0 scope: ``TokenUsage`` is populated with placeholder
``cost_usd=Decimal("0")``. Task 5b wires real pricing.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel

from cyberlab_gen.errors import CyberlabGenError
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    Provider,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolExecutor,
)

_MESSAGE_EXCERPT_CHARS = 200


class UnmatchedMockCall(CyberlabGenError):  # noqa: N818 -- name locked by provider-interface.md §7.1
    """A call into ``MockProvider`` had no matching registered response.

    Subclasses ``CyberlabGenError`` directly, NOT ``ProviderError``:
    production code that catches ``ProviderError`` must not silently
    swallow test-infrastructure signals. Failing loudly when a test gap
    exists is the entire point of this class.
    """


@dataclass(frozen=True)
class _MockRegistration:
    """One registered (capability, agent_label, matcher) → response binding."""

    capability: CapabilityHint
    agent_label: AgentLabel
    response: BaseModel
    usage: TokenUsage | None
    tool_calls: list[ToolCall] = field(default_factory=list[ToolCall])
    message_matcher: Callable[[list[Message]], bool] | None = None


def _default_usage() -> TokenUsage:
    return TokenUsage(input_tokens=0, output_tokens=0, cost_usd=Decimal("0"))


def _excerpt(messages: list[Message]) -> str:
    joined = " | ".join(m.content for m in messages if m.content)
    if len(joined) <= _MESSAGE_EXCERPT_CHARS:
        return joined
    return joined[:_MESSAGE_EXCERPT_CHARS] + "..."


class MockProvider(Provider):
    """Test-only ``Provider`` returning canned responses.

    Registration order matters: ``complete()`` walks registrations in the
    order they were added and returns the first match on
    ``(capability, agent_label, message_matcher)``. Tests that need
    deterministic dispatch should either register a single response or
    use a matcher to disambiguate.
    """

    def __init__(self) -> None:
        self._registrations: list[_MockRegistration] = []
        self._default_usage: TokenUsage | None = None

    @property
    def name(self) -> str:
        return "mock"

    def register(
        self,
        *,
        capability: CapabilityHint,
        agent_label: AgentLabel,
        response: BaseModel,
        message_matcher: Callable[[list[Message]], bool] | None = None,
        usage: TokenUsage | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        """Register a canned response.

        When ``usage`` is ``None``, falls back to whatever was set via
        ``register_default_usage()``; if neither is set, a minimal
        zero-cost ``TokenUsage`` is used at call time.
        """
        self._registrations.append(
            _MockRegistration(
                capability=capability,
                agent_label=agent_label,
                response=response,
                usage=usage,
                tool_calls=list(tool_calls) if tool_calls else [],
                message_matcher=message_matcher,
            )
        )

    def register_default_usage(self, usage: TokenUsage) -> None:
        """Set the ``TokenUsage`` used when ``register()`` omits ``usage``."""
        self._default_usage = usage

    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        del max_tokens  # mock does not constrain output length
        registration = self._find(messages, capability=capability, agent_label=agent_label)
        return self._build_response(
            messages=messages,
            registration=registration,
            output_schema=output_schema,
            capability=capability,
            agent_label=agent_label,
        )

    async def complete_with_tools[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        del tools, tool_executor, max_iterations, max_tokens
        registration = self._find(messages, capability=capability, agent_label=agent_label)
        return self._build_response(
            messages=messages,
            registration=registration,
            output_schema=output_schema,
            capability=capability,
            agent_label=agent_label,
        )

    def _find(
        self,
        messages: list[Message],
        *,
        capability: CapabilityHint,
        agent_label: AgentLabel,
    ) -> _MockRegistration:
        for registration in self._registrations:
            if registration.capability is not capability:
                continue
            if registration.agent_label is not agent_label:
                continue
            if registration.message_matcher is not None and not registration.message_matcher(
                messages
            ):
                continue
            return registration
        raise UnmatchedMockCall(
            f"no matching mock registration for capability={capability.value!r}, "
            f"agent_label={agent_label.value!r}; messages excerpt: {_excerpt(messages)!r}"
        )

    def _build_response[T_Output: BaseModel](
        self,
        *,
        messages: list[Message],
        registration: _MockRegistration,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
    ) -> ProviderResponse[T_Output]:
        if not isinstance(registration.response, output_schema):
            raise UnmatchedMockCall(
                f"mock registration for capability={capability.value!r}, "
                f"agent_label={agent_label.value!r} has response of type "
                f"{type(registration.response).__name__}, but call requested "
                f"output_schema={output_schema.__name__}"
            )
        output: T_Output = registration.response
        usage = registration.usage or self._default_usage or _default_usage()
        raw_text = output.model_dump_json()
        final_message = Message(role=MessageRole.ASSISTANT, content=raw_text)
        return ProviderResponse[T_Output](
            output=output,
            raw_text=raw_text,
            usage=usage,
            model="mock-canned",
            provider=self.name,
            conversation=[*messages, final_message],
            tool_calls=list(registration.tool_calls),
        )
