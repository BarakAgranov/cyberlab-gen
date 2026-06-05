"""The agent-facing provider call surface (capability-hint dispatch).

Per pipeline.md §3.5 and provider-interface.md §3-§4: agent code never names a
model. It requests a *capability hint* and a typed output schema; this surface
resolves the highest-ranked reachable model via the ranking file, calls through
the Provider interface, and returns a validated typed object.

Structured-output enforcement lives here at the agent/provider boundary: when
the provider exhausts its own malformed-output retries and raises
MalformedOutput, this surface retries the whole stage up to a structural-retry
budget and, on exhaustion, raises AgentFailure (the agent-failure path). This is
*structural* retry, never refinement (architecture.md §1.7, ADR 0018).

The orchestrator (Task 6) owns control flow: this surface produces content and
raises typed failures; it never decides whether to refine or whether output
ships (architecture.md §1.5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from cyberlab_gen.agents.prompts import load_prompt
from cyberlab_gen.errors import AgentFailure, EmitTruncated, MalformedOutput
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    Provider,
    ProviderResponse,
    ToolDefinition,
    ToolExecutor,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from cyberlab_gen.providers.ranking import ProviderRegistry

logger = logging.getLogger(__name__)

#: Default number of *additional* stage attempts after the first, on
#: MalformedOutput. Total attempts = 1 + DEFAULT_STRUCTURAL_RETRY_ATTEMPTS.
#: Placeholder per ADR 0018; calibrated from eval data in a later phase.
DEFAULT_STRUCTURAL_RETRY_ATTEMPTS = 2


class _StructuredCall[T_Output: BaseModel](Protocol):
    """A zero-arg coroutine producing a typed ProviderResponse.

    Used to share the structural-retry loop between the no-tools and tool-using
    call paths without duplicating it.
    """

    async def __call__(self) -> ProviderResponse[T_Output]: ...


class AgentRunner:
    """Capability-hint dispatch over a Provider for one agent role.

    An AgentRunner is bound to a single agent role (its AgentLabel and the
    agent directory holding its prompt files) and a Provider + ProviderRegistry.
    Agent implementations (Tasks 3/5) construct one and call `run` /
    `run_with_tools`.
    """

    def __init__(
        self,
        *,
        agent_label: AgentLabel,
        agent_dir: str,
        provider: Provider,
        registry: ProviderRegistry,
        structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    ) -> None:
        if structural_retry_attempts < 0:
            raise ValueError("structural_retry_attempts must be >= 0")
        self._agent_label = agent_label
        self._agent_dir = agent_dir
        self._provider = provider
        self._registry = registry
        self._structural_retry_attempts = structural_retry_attempts

    @property
    def agent_label(self) -> AgentLabel:
        return self._agent_label

    def resolve_model(self, capability: CapabilityHint) -> tuple[str, str]:
        """Resolve a capability hint to ``(provider_name, model_id)``.

        Resolution goes through the ranking file: the highest-ranked entry whose
        provider is configured/reachable wins. Raises CapabilityUnreachable
        (from the registry) when no entry is reachable. Logged at INFO for the
        run report's cost-per-quality metric (provider-interface.md §3.4).
        """
        entry = self._registry.resolve(capability)
        logger.info(
            "agent %s resolved capability %s to %s/%s",
            self._agent_label.value,
            capability.value,
            entry.provider,
            entry.model,
        )
        return (entry.provider, entry.model)

    def build_messages(
        self,
        *,
        capability: CapabilityHint,
        user_content: str,
        system_override: str | None = None,
    ) -> list[Message]:
        """Build the message list: system prompt (base + model overlay) + user turn.

        The system prompt is loaded via the base-prompt-plus-overlay loader, keyed
        by the model the capability resolves to (pipeline.md §3.5). Pass
        ``system_override`` to supply the system prompt directly (tests, or agents
        that build prompts in code rather than files).
        """
        if system_override is not None:
            system_text = system_override
        else:
            _, model = self.resolve_model(capability)
            system_text = load_prompt(self._agent_dir, model=model)
        return [
            Message(role=MessageRole.SYSTEM, content=system_text),
            Message(role=MessageRole.USER, content=user_content),
        ]

    async def run[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        """Single-turn structured call. Returns a validated typed response.

        On MalformedOutput from the provider (its own malformed-output retries
        already exhausted), retries the stage up to the structural-retry budget;
        on exhaustion raises AgentFailure (ADR 0018).
        """

        async def _call() -> ProviderResponse[T_Output]:
            return await self._provider.complete(
                messages,
                output_schema=output_schema,
                capability=capability,
                agent_label=self._agent_label,
                max_tokens=max_tokens,
            )

        return await self._with_structural_retry(_call, capability=capability)

    async def run_with_tools[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        """Tool-using-loop structured call. Same structural-retry semantics as `run`."""

        async def _call() -> ProviderResponse[T_Output]:
            return await self._provider.complete_with_tools(
                messages,
                output_schema=output_schema,
                capability=capability,
                tools=tools,
                tool_executor=tool_executor,
                agent_label=self._agent_label,
                max_iterations=max_iterations,
                max_tokens=max_tokens,
            )

        return await self._with_structural_retry(_call, capability=capability)

    async def _with_structural_retry[T_Output: BaseModel](
        self,
        call: _StructuredCall[T_Output],
        *,
        capability: CapabilityHint,
    ) -> ProviderResponse[T_Output]:
        max_attempts = 1 + self._structural_retry_attempts
        last_error: MalformedOutput | None = None
        prev_signature: str | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await call()
            except EmitTruncated:
                # A truncated emit (ADR 0033) is a non-retryable halt, NOT a
                # structural-retry: re-running regenerates the same oversized output
                # and truncates again at the same token budget, so retrying only
                # burns money. Re-raise past the structural-retry budget entirely so
                # the run fails fast with the truncation halt_reason (raise max_tokens
                # or shorten the input) instead of the generic "exhausted budget" one.
                # Caught before the MalformedOutput handler because it subclasses it.
                raise
            except MalformedOutput as exc:
                last_error = exc
                logger.warning(
                    "agent %s structural malformation on attempt %d/%d (capability %s): %s",
                    self._agent_label.value,
                    attempt,
                    max_attempts,
                    capability.value,
                    exc,
                )
                # No-progress early-bail (ADR 0032): a structural failure identical
                # to the previous attempt's means the model is reproducing the same
                # invalid output — more retries cannot make progress and only cost
                # money. Stop now rather than exhausting the budget. A *different*
                # failure may signal convergence, so the full budget is honoured then.
                signature = str(exc)
                if signature == prev_signature:
                    logger.warning(
                        "agent %s structural failure repeated identically; aborting retries "
                        "early after %d/%d attempts (capability %s)",
                        self._agent_label.value,
                        attempt,
                        max_attempts,
                        capability.value,
                    )
                    break
                prev_signature = signature
        raise AgentFailure(
            f"agent {self._agent_label.value} exhausted structural-retry budget "
            f"({max_attempts} attempts) for capability {capability.value}"
        ) from last_error
