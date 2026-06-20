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
    from cyberlab_gen.external_data_sources import NvdClient
    from cyberlab_gen.providers.base import (
        AgentLabel,
        CapabilityHint,
        Provider,
        ProviderResponse,
        ToolDefinition,
    )
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec


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

    def _build_tools_and_executor(
        self, *, offer_external_lookup: bool = True
    ) -> tuple[list[ToolDefinition], ExtractorToolExecutor]:
        """Build the advertised tools + the executor for one emit (the overridable inventory seam).

        The default is the Phase-1 inventory: the Extractor's tools and ``ExtractorToolExecutor``,
        gated by ``verify_only_tools`` — so the Extractor (producer) and the Extractor-Jury
        (verify-only, ADR 0078) keep their exact behaviour without overriding this. A producer
        whose tool set is **not** the Extractor's (the Planner, `agents.md §5.7`; the Phase-3
        Generators) overrides this to return its own ``(tools, executor)``; the six-step sequence
        and the ``§1.5`` invariants in :meth:`_emit` stay untouched (ADR 0089). The override's
        executor is an ``ExtractorToolExecutor`` *subtype*, so :meth:`_emit`'s return type holds.
        """
        source_ids = sorted(e.id for e in self._registries.external_data_sources.entries)
        executor = ExtractorToolExecutor(
            registries=self._registries,
            nvd_client=self._nvd_client,
            verify_only=self._verify_only_tools,
        )
        tools = extractor_tool_definitions(
            registered_source_ids=source_ids,
            verify_only=self._verify_only_tools,
            offer_external_lookup=offer_external_lookup,
        )
        return tools, executor

    async def _emit[T: BaseModel](
        self,
        *,
        capability: CapabilityHint,
        output_schema: type[T],
        user_content: str,
        max_tokens: int | None = None,
        offer_external_lookup: bool = True,
    ) -> tuple[ProviderResponse[T], ExtractorToolExecutor]:
        """Run one tool-loop emit and return ``(response, executor)``.

        The six steps, once: build the (tools, executor) via the overridable
        :meth:`_build_tools_and_executor` seam, build the messages, run the tool loop with the
        forced typed output, and hand back the validated response **and** the executor (so a caller
        can read the executor's side-channel — proposals + lookups — or ignore it). The model's
        output is returned, never consulted here for routing (``architecture.md §1.5``).
        """
        tools, executor = self._build_tools_and_executor(
            offer_external_lookup=offer_external_lookup
        )
        messages = self._runner.build_messages(capability=capability, user_content=user_content)
        response = await self._runner.run_with_tools(
            messages,
            output_schema=output_schema,
            capability=capability,
            tools=tools,
            tool_executor=executor,
            max_iterations=self._max_tool_iterations,
            max_tokens=max_tokens,
        )
        return response, executor


def verify_only_external_lookup_offered(*, nvd_client_wired: bool, spec: AttackSpec) -> bool:
    """Whether a verify-only agent (the juries) should be offered ``external_lookup`` (ADR 0105).

    The verify-only executor can serve only NVD this phase (every other source returns unavailable),
    so there is verifiable work iff an NVD client is wired AND the spec carries >= 1 CVE for it to
    check. With no client (today's jury wiring) or no CVE (e.g. the codebuild blog), the dead tool is
    withheld so the jury emits its verdict rather than walking the whole source catalog into a
    ``ToolLoopError`` (the run-20260620 Planner-Jury spiral). Generalises as more sources gain live
    executor paths: extend the predicate to "any integrated source has a matching spec value".
    """
    refs = spec.external_references
    return nvd_client_wired and refs is not None and len(refs.cves) > 0


__all__ = ["ToolUsingAgent", "verify_only_external_lookup_offered"]
