"""The reusable tool-using agent contract — the six-step emit sequence, owned once.

Architectural source: ``architecture.md §1.5`` (LLMs produce content / structured judgments;
the framework owns control flow), ``agents.md §5.4``/``§5.5``. ADR 0072.

Both Phase-1 typed-output agents — the Extractor (``output_type=AttackSpec`` /
``RefinementPatch``) and the Extractor-Jury (``output_type=JuryVerdict``) — drive a tool loop over
the *same* registries and the *same* (Extractor) tool inventory, and each hand-rolled the identical
six steps: type-guard the registries, derive the registered source ids, build the
``ExtractorToolExecutor``, build the system+user messages, ``run_with_tools``, and read back the
typed response. That copy-paste is exactly where the ``§1.5`` invariants could drift between agents.

:class:`ToolUsingAgent` owns those steps once, *above* :class:`~cyberlab_gen.agents.call_surface.AgentRunner`
(which owns the call mechanics). Subclasses supply only what differs — the capability, the output
schema, the user turn, and the output cap — and read the typed result. The invariants live here:

- ``registries`` is typed ``MergedRegistries`` (no ``object`` + runtime ``isinstance`` guard — the
  ``registries`` package imports neither ``agents`` nor ``framework``, so the type is safe);
- the model's typed output is **returned as data**; this contract never inspects it to decide
  control flow (that is the orchestrator's job, ``§1.5``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from cyberlab_gen.agents.call_surface import AgentRunner
from cyberlab_gen.agents.extractor.tools import (
    ExtractorToolExecutor,
    extractor_tool_definitions,
)

if TYPE_CHECKING:
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import (
        AgentLabel,
        CapabilityHint,
        Provider,
        ProviderResponse,
    )
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries


class ToolUsingAgent:
    """Base for typed-output agents that drive the Extractor tool loop over the merged registries.

    Holds the shared :class:`AgentRunner` (call mechanics) and the merged registries, and exposes
    :meth:`_emit` — the one place the six-step sequence lives. The Extractor-Jury shares the
    Extractor's tool inventory so it can independently verify ``external_api`` responses
    (``agents.md §5.5``), which is why the executor + tool definitions are common to both.
    """

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: MergedRegistries,
        agent_label: AgentLabel,
        agent_dir: str,
        max_tool_iterations: int,
        nvd_client: NvdClient | None = None,
        verify_only_tools: bool = False,
    ) -> None:
        self._runner = AgentRunner(
            agent_label=agent_label,
            agent_dir=agent_dir,
            provider=provider,
            registry=registry,
        )
        self._registries = registries
        self._nvd_client = nvd_client
        self._max_tool_iterations = max_tool_iterations
        #: A review-only agent (the Extractor-Jury; ADR 0078) is advertised only the read/verify
        #: tools and its executor refuses the propose_* write tools — the §1.5 read/write split
        #: enforced by tool availability, not prose.
        self._verify_only_tools = verify_only_tools

    async def _emit[T: BaseModel](
        self,
        *,
        capability: CapabilityHint,
        output_schema: type[T],
        user_content: str,
        max_tokens: int | None = None,
    ) -> tuple[ProviderResponse[T], ExtractorToolExecutor]:
        """Run one tool-loop emit and return ``(response, executor)``.

        The six steps, once: derive the registered source ids, build the tool executor, build the
        messages, run the tool loop with the forced typed output, and hand back the validated
        response **and** the executor (so a caller can read the executor's side-channel —
        proposals + lookups — or ignore it). The model's output is returned, never consulted here
        for routing (``architecture.md §1.5``).
        """
        source_ids = sorted(e.id for e in self._registries.external_data_sources.entries)
        executor = ExtractorToolExecutor(
            registries=self._registries,
            nvd_client=self._nvd_client,
            verify_only=self._verify_only_tools,
        )
        messages = self._runner.build_messages(capability=capability, user_content=user_content)
        response = await self._runner.run_with_tools(
            messages,
            output_schema=output_schema,
            capability=capability,
            tools=extractor_tool_definitions(
                registered_source_ids=source_ids, verify_only=self._verify_only_tools
            ),
            tool_executor=executor,
            max_iterations=self._max_tool_iterations,
            max_tokens=max_tokens,
        )
        return response, executor


__all__ = ["ToolUsingAgent"]
