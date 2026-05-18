"""Integration tests for ``MockProvider``.

Covers the §7 contract: ``register()`` happy path, message matchers,
``register_default_usage()`` fallback, ``UnmatchedMockCall`` on missing
registrations, and the ``name`` property.

Uses ``asyncio.run`` directly rather than ``pytest-asyncio`` — the
async-plugin dependency is deferred to Phase 1+ per the Task 0
execution-log note.
"""

import asyncio
from decimal import Decimal

import pytest
from pydantic import BaseModel

from cyberlab_gen.providers import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    MockProvider,
    TokenUsage,
    UnmatchedMockCall,
)


class _PlanOutput(BaseModel):
    summary: str
    steps: list[str]


class _OtherOutput(BaseModel):
    note: str


def _usage(input_tokens: int = 12, output_tokens: int = 34) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal("0"),
    )


def _user(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


def test_mock_provider_name() -> None:
    assert MockProvider().name == "mock"


def test_complete_happy_path() -> None:
    provider = MockProvider()
    plan = _PlanOutput(summary="phish", steps=["recon", "deliver"])
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=plan,
        usage=_usage(),
    )
    response = asyncio.run(
        provider.complete(
            [_user("plan an attack")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    assert response.output == plan
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 34
    assert response.provider == "mock"
    assert response.model == "mock-canned"
    assert response.conversation[0].content == "plan an attack"
    assert response.conversation[-1].role is MessageRole.ASSISTANT


def test_complete_uses_message_matcher() -> None:
    provider = MockProvider()
    first = _PlanOutput(summary="first", steps=[])
    second = _PlanOutput(summary="second", steps=[])
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        message_matcher=lambda msgs: any("alpha" in m.content for m in msgs),
        response=first,
    )
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        message_matcher=lambda msgs: any("beta" in m.content for m in msgs),
        response=second,
    )
    alpha = asyncio.run(
        provider.complete(
            [_user("contains alpha")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    beta = asyncio.run(
        provider.complete(
            [_user("contains beta")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    assert alpha.output.summary == "first"
    assert beta.output.summary == "second"


def test_register_default_usage_is_applied_when_registration_omits_it() -> None:
    provider = MockProvider()
    provider.register_default_usage(_usage(input_tokens=99, output_tokens=88))
    provider.register(
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.CRITIC,
        response=_OtherOutput(note="ok"),
    )
    response = asyncio.run(
        provider.complete(
            [_user("hi")],
            output_schema=_OtherOutput,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            agent_label=AgentLabel.CRITIC,
        )
    )
    assert response.usage.input_tokens == 99
    assert response.usage.output_tokens == 88


def test_unmatched_call_raises_with_context() -> None:
    provider = MockProvider()
    with pytest.raises(UnmatchedMockCall) as info:
        asyncio.run(
            provider.complete(
                [_user("totally unregistered request body")],
                output_schema=_OtherOutput,
                capability=CapabilityHint.HIGH_QUALITY_REASONING,
                agent_label=AgentLabel.EXTRACTOR,
            )
        )
    msg = str(info.value)
    assert "high_quality_reasoning" in msg
    assert "extractor" in msg
    assert "totally unregistered request body" in msg


def test_response_type_mismatch_raises_unmatched() -> None:
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=_PlanOutput(summary="x", steps=[]),
    )
    with pytest.raises(UnmatchedMockCall, match="output_schema"):
        asyncio.run(
            provider.complete(
                [_user("hi")],
                output_schema=_OtherOutput,
                capability=CapabilityHint.HIGH_QUALITY_REASONING,
                agent_label=AgentLabel.PLANNER,
            )
        )


def test_mock_provider_fills_cost_usd_from_pricing_table() -> None:
    """When a real model is registered and ``cost_usd`` is the placeholder,
    the mock fills the cost from the bundled pricing table.

    Task 5b deliverable: the mock provider's ``cost_usd`` is no longer
    a frozen-zero placeholder when callers attach a real Anthropic model
    name; the value is computed via ``compute_cost`` against the bundled
    pricing rows.
    """
    from cyberlab_gen.providers import compute_cost, load_pricing_table

    provider = MockProvider()
    plan = _PlanOutput(summary="phish", steps=["recon"])
    usage_input = TokenUsage(
        input_tokens=2_000,
        output_tokens=1_000,
        cost_usd=Decimal("0"),
    )
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=plan,
        usage=usage_input,
        model="claude-opus-4-7",
    )
    response = asyncio.run(
        provider.complete(
            [_user("plan an attack")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    expected_cost = compute_cost(
        load_pricing_table(),
        provider="anthropic",
        model="claude-opus-4-7",
        usage=usage_input,
    )
    assert response.usage.cost_usd == expected_cost
    assert response.usage.cost_usd > Decimal("0")
    assert response.model == "claude-opus-4-7"
    assert response.provider == "mock"  # still the mock; only the model label changes


def test_mock_provider_preserves_explicit_cost_usd() -> None:
    """A caller-supplied non-zero ``cost_usd`` is never overwritten.

    Useful for crafting test fixtures with specific roll-up values
    independent of the bundled pricing.
    """
    provider = MockProvider()
    plan = _PlanOutput(summary="phish", steps=[])
    explicit = TokenUsage(
        input_tokens=10,
        output_tokens=10,
        cost_usd=Decimal("99.99"),
    )
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=plan,
        usage=explicit,
        model="claude-opus-4-7",
    )
    response = asyncio.run(
        provider.complete(
            [_user("hi")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    assert response.usage.cost_usd == Decimal("99.99")


def test_mock_provider_default_model_keeps_cost_zero() -> None:
    """Without an explicit ``model`` argument the response still reports
    ``model='mock-canned'`` and the placeholder zero cost — back-compat
    with every Task 5a-era registration that does not opt in to pricing.
    """
    provider = MockProvider()
    plan = _PlanOutput(summary="phish", steps=[])
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.PLANNER,
        response=plan,
        usage=TokenUsage(input_tokens=100, output_tokens=100, cost_usd=Decimal("0")),
    )
    response = asyncio.run(
        provider.complete(
            [_user("hi")],
            output_schema=_PlanOutput,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            agent_label=AgentLabel.PLANNER,
        )
    )
    assert response.model == "mock-canned"
    assert response.usage.cost_usd == Decimal("0")
