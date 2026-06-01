"""Live ``AnthropicProvider`` test — a REAL Anthropic API call, recorded once.

This is the test the task brief insists on: mock-based unit tests
(``tests/unit/providers/test_anthropic_provider.py``) verify the loop logic, but
the mock is exactly what hid the Phase-0 stub. This test makes an *actual*
Anthropic Messages API call through the real ``anthropic.AsyncAnthropic`` client,
confirms the response parses into a typed schema with real (non-zero) cost, and
records the interaction as a pytest-recording cassette so the real-call path is
regression-tested offline forever.

Re-record (after deleting the cassette) with a live key:

    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    uv run pytest tests/integration/test_anthropic_provider_live.py --record-mode=once

The cassette at ``tests/integration/cassettes/test_anthropic_provider_live/`` has
the API key filtered out (see ``conftest.vcr_config``). The model used is
``claude-haiku-4-5-20251001`` (capability ``fast_cheap_structured_output``) — a
real, cheap, currently-served model that is present in ``pricing.yaml`` so cost
computes.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import anthropic
import pytest
from pydantic import BaseModel

from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
)

_HAIKU = "claude-haiku-4-5-20251001"

#: pytest-recording stores the cassette here (``<dir>/cassettes/<module>/<test>.yaml``).
_CASSETTE = (
    Path(__file__).parent
    / "cassettes"
    / "test_anthropic_provider_live"
    / "test_complete_against_real_anthropic_api.yaml"
)


class Greeting(BaseModel):
    """Tiny structured-output schema for the live smoke call."""

    greeting: str
    audience: str


@pytest.mark.vcr
async def test_complete_against_real_anthropic_api() -> None:
    # Replay from the committed cassette offline; record live with a real key.
    # When NEITHER exists, skip loudly rather than (a) erroring the whole suite or
    # (b) silently passing — the recorded cassette is the real-call regression proof.
    if not _CASSETTE.exists() and not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        pytest.skip(
            "No recorded cassette and ANTHROPIC_API_KEY is unset. Record the real call with: "
            "uv run pytest tests/integration/test_anthropic_provider_live.py --record-mode=once"
        )

    # The anthropic SDK client requires *a* key to construct, even when the HTTP
    # call is served from a VCR cassette. With a real key present we exercise the
    # default lazy-client path (recording); during offline replay we inject a
    # client built with a non-functional placeholder — VCR serves the recorded
    # response, so the placeholder never reaches the network.
    real_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    provider = (
        AnthropicProvider()
        if real_key
        else AnthropicProvider(
            client=anthropic.AsyncAnthropic(api_key="placeholder-not-a-real-key")
        )
    )
    messages = [
        Message(
            role=MessageRole.SYSTEM,
            content="You are a terse assistant that returns structured data.",
        ),
        Message(
            role=MessageRole.USER,
            content="Produce a short greeting addressed to the whole world.",
        ),
    ]

    response = await provider.complete(
        messages,
        output_schema=Greeting,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
        max_tokens=256,
    )

    # Structured output really parsed.
    assert isinstance(response.output, Greeting)
    assert response.output.greeting.strip()
    assert response.output.audience.strip()

    # Provenance of the call is correct.
    assert response.provider == "anthropic"
    assert response.model == _HAIKU

    # Real token usage and a real, non-zero cost (not the placeholder Decimal("0")).
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.usage.cost_usd > Decimal("0")

    # Conversation trace ends with the assistant's structured answer.
    assert response.conversation[-1].role is MessageRole.ASSISTANT
    assert response.conversation[-1].content == response.raw_text
