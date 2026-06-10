"""Tests for the shared tool-using agent contract (ADR 0072).

Pins that the Extractor and the Extractor-Jury both go through :class:`ToolUsingAgent` (so the
six-step tool-loop sequence + the §1.5 invariants live in exactly one place), and that the
contract's ``_emit`` drives the call surface and hands back both the typed response and the
executor side-channel.
"""

from __future__ import annotations

from pydantic import BaseModel

from cyberlab_gen.agents.extractor.extractor import Extractor
from cyberlab_gen.agents.extractor.tools import ExtractorToolExecutor
from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
from cyberlab_gen.agents.tool_agent import ToolUsingAgent
from cyberlab_gen.providers import (
    AgentLabel,
    CapabilityHint,
    MockProvider,
    ModelRankings,
    ProviderRegistry,
    ProviderResponse,
)
from cyberlab_gen.registries.merge import load_merged_registries


def test_extractor_and_jury_share_the_tool_using_agent_contract() -> None:
    """Both Phase-1 tool-using agents are refactored onto the one contract (ADR 0072)."""
    assert issubclass(Extractor, ToolUsingAgent)
    assert issubclass(ExtractorJury, ToolUsingAgent)


class _Out(BaseModel):
    value: str


class _MiniAgent(ToolUsingAgent):
    """A minimal concrete agent that just exposes the protected ``_emit`` for the test."""

    async def emit(
        self, *, user_content: str
    ) -> tuple[ProviderResponse[_Out], ExtractorToolExecutor]:
        return await self._emit(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            output_schema=_Out,
            user_content=user_content,
        )


async def test_emit_drives_the_tool_loop_and_returns_response_and_executor() -> None:
    """The single six-step seam runs through the call surface and returns ``(response, executor)``:
    the typed output for any agent, plus the executor side-channel the Extractor reads.
    """
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.EXTRACTOR,
        response=_Out(value="x"),
    )
    rankings = ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ]
            }
        }
    )
    agent = _MiniAgent(
        provider=provider,
        registry=ProviderRegistry(rankings, frozenset({"anthropic"})),
        registries=load_merged_registries(),
        agent_label=AgentLabel.EXTRACTOR,
        agent_dir="extractor",
        max_tool_iterations=4,
    )
    response, executor = await agent.emit(user_content="hi")

    assert response.output == _Out(value="x")  # the typed response flowed through run_with_tools
    assert provider.last_model == "model-x"  # the registry-resolved model reached the provider
    assert isinstance(executor, ExtractorToolExecutor)  # the side-channel is handed back
