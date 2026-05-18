"""Mock provider for tests — canned responses, no real API calls.

Architectural source: ``provider-interface.md`` §7. This is the
load-bearing test fixture for the entire project: every agent's unit
tests register canned responses and assert on the agent's behavior
against them. Unmatched calls raise ``UnmatchedMockCall`` so test gaps
fail loudly rather than hanging or returning empty data.

Task 5b wired pricing into the response path: callers may pass a
``model`` argument to :meth:`MockProvider.register` (defaulting to the
Phase-0 ``"mock-canned"`` sentinel). When a real Anthropic model
identifier is supplied and the registered ``usage.cost_usd`` is the
placeholder ``Decimal("0")``, the response is rebuilt with
``cost_usd`` computed from the bundled pricing table. Callers that
pre-set ``cost_usd`` (for example, to test rollups with crafted
values) keep their value untouched.

The ``provider`` field on the returned response stays ``"mock"`` — the
mock is still the mock — while the ``model`` field reflects whatever
the caller registered, so tests that round-trip cost computations see
a coherent ``(provider="anthropic", model)`` pricing lookup match the
response's reported model.
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
from cyberlab_gen.providers.cost_ledger import (
    PricingTable,
    compute_cost,
    load_pricing_table,
)

_PRICING_PROVIDER = "anthropic"
_DEFAULT_MOCK_MODEL = "mock-canned"

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
    model: str = _DEFAULT_MOCK_MODEL
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
        self._pricing_table: PricingTable | None = None

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
        model: str | None = None,
    ) -> None:
        """Register a canned response.

        When ``usage`` is ``None``, falls back to whatever was set via
        ``register_default_usage()``; if neither is set, a minimal
        zero-cost ``TokenUsage`` is used at call time.

        ``model`` controls two things about the response:

        - The ``ProviderResponse.model`` field. Defaults to the
          ``"mock-canned"`` Phase-0 sentinel so existing callers keep
          their current behavior.
        - Pricing lookup. When ``model`` is a real Anthropic model
          identifier present in the bundled pricing table AND the
          effective ``usage.cost_usd`` is the placeholder ``Decimal("0")``,
          the response's usage is rebuilt with the cost computed from
          the table. Callers that pre-set a non-zero ``cost_usd`` keep
          their value (useful for crafting rollup test fixtures).
        """
        self._registrations.append(
            _MockRegistration(
                capability=capability,
                agent_label=agent_label,
                response=response,
                usage=usage,
                model=model if model is not None else _DEFAULT_MOCK_MODEL,
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
        usage = self._maybe_fill_cost(usage, model=registration.model)
        raw_text = output.model_dump_json()
        final_message = Message(role=MessageRole.ASSISTANT, content=raw_text)
        return ProviderResponse[T_Output](
            output=output,
            raw_text=raw_text,
            usage=usage,
            model=registration.model,
            provider=self.name,
            conversation=[*messages, final_message],
            tool_calls=list(registration.tool_calls),
        )

    def _maybe_fill_cost(self, usage: TokenUsage, *, model: str) -> TokenUsage:
        """Compute ``cost_usd`` from the pricing table when applicable.

        Returns ``usage`` unchanged when:
        - The registration's ``model`` is the mock sentinel (no pricing
          lookup makes sense for ``mock-canned``).
        - The caller pre-set a non-zero ``cost_usd`` — that is taken as
          the authoritative figure for the test.
        - The model is not present in the pricing table.
        """
        if model == _DEFAULT_MOCK_MODEL or usage.cost_usd != Decimal("0"):
            return usage
        table = self._get_pricing_table()
        try:
            cost = compute_cost(
                table,
                provider=_PRICING_PROVIDER,
                model=model,
                usage=usage,
            )
        except KeyError:
            return usage
        return usage.model_copy(update={"cost_usd": cost})

    def _get_pricing_table(self) -> PricingTable:
        if self._pricing_table is None:
            self._pricing_table = load_pricing_table()
        return self._pricing_table
