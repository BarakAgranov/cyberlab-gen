"""Anthropic provider — Phase 0 scaffold.

Architectural source: ``provider-interface.md`` §8.1. This module exists
so Phase 1's first task can fill in the call bodies without redesigning
the surface. Both call methods raise ``NotImplementedError("Phase 1")``.

The ``anthropic`` SDK is imported at module load to prove the dependency
resolves (the package is pinned at ``anthropic>=0.40`` in ``pyproject.toml``).
"""

import anthropic
from pydantic import BaseModel

from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    Provider,
    ProviderResponse,
    ToolDefinition,
    ToolExecutor,
)

# Probe that the SDK resolves at module load — Phase 1's adapter body will use it.
_ANTHROPIC_SDK = anthropic


class AnthropicProvider(Provider):
    """Anthropic adapter. Phase 0 scaffold; Phase 1 fills in the call bodies."""

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        del messages, output_schema, capability, agent_label, max_tokens
        raise NotImplementedError("Phase 1")

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
        del (
            messages,
            output_schema,
            capability,
            tools,
            tool_executor,
            agent_label,
            max_iterations,
            max_tokens,
        )
        raise NotImplementedError("Phase 1")
