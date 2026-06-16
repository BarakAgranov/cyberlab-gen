"""The Planner stage: derive a draft LabManifest skeleton from an enriched AttackSpec.

Architectural source: ``agents.md §5.7`` (Planner job, inputs, outputs, provenance discipline,
quality bar), ``pipeline.md §3.2.6`` (the Planner stage), ``architecture.md §0.7`` (the lab class
is **emergent** — per-step tiers carried forward, structural realization decided per step),
``§1.5`` (LLMs produce content; the framework derives). ADR 0072 (the ``ToolUsingAgent`` contract),
ADR 0089 (the tool-provider hook — the Planner's producer tool set), ADR 0090 (output schema /
derive-at-seam), ADR 0081/0088 (per-step placement + lab-level derivation).

The Planner is a typed-output **producer** (output type ``LabManifest``) invoked via the
capability-hint call surface (``high_quality_reasoning`` — the most decision-dense stage against the
largest schema, ``agents.md §5.7``). It **organizes** the AttackSpec's already-established content
into an implementation skeleton; it does not invent content, repair the AttackSpec, propose value
types (Extractor authority, ``schema.md §4.16``), or re-evaluate per-step reproducibility
(``§0.7`` — the Extractor assigned the tier; the Planner carries it forward *unchanged*).

For this Wave-1 slice the Planner is **non-proposing** (``propose_facet`` is Task 7) and its only
wired tool is the read-only ``external_lookup`` (:mod:`cyberlab_gen.agents.planner.tools`).

:meth:`plan` runs one emit and finalizes the manifest: the **lab-level** ``core.reproducibility`` is
**framework-derived** from the AttackSpec's per-step tiers (``derive_lab_reproducibility``,
ADR 0088), never the LLM's value, so ``plan`` overwrites it before returning (ADR 0090). The other
framework-owned fields (``spec_version``, ``GenerationBlock.model``) are stamped at the persist seam
(``state/run_persistence.py``), wired in Task 6 — mirroring how the Extractor defers its own stamps
to persistence (ADR 0068/0069). The Planner↔Jury revise loop and the AttackSpec-incoherence
route-back are Task 4; the ``plan`` verb / graph wiring + persistence are Task 6.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor.extractor import build_registry_digest
from cyberlab_gen.agents.planner.tools import PlannerToolExecutor, planner_tool_definitions
from cyberlab_gen.agents.results import PlanResult
from cyberlab_gen.agents.tool_agent import ToolUsingAgent
from cyberlab_gen.framework.reproducibility import derive_lab_reproducibility
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
from cyberlab_gen.schemas.manifest import LabManifest

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor.tools import ExtractorToolExecutor
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider, ToolDefinition
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec

logger = logging.getLogger(__name__)

PLANNER_AGENT_DIR = "planner"

#: Tool-use loop depth for one Planner pass (mirrors the Extractor; ``provider-interface.md §4.1``).
DEFAULT_MAX_TOOL_ITERATIONS = 12

#: Output-token budget for the LabManifest emit. The Planner emits the entire manifest as one tool
#: call, so the same non-streaming wall the Extractor hit applies (``DEFAULT_EXTRACTOR_MAX_TOKENS`` —
#: max_tokens above ~21_333 raises ``ValueError`` in the Anthropic SDK). 20000 stays below it with
#: headroom; a class-fix for very large manifests (streaming + sectioned emit) is deferred with the
#: Extractor's (``implementation-plan.md §4.6``). Placeholder per ``architecture.md §8.4``.
DEFAULT_PLANNER_MAX_TOKENS = 20000


class Planner(ToolUsingAgent):
    """Drives one Planner stage run over an enriched AttackSpec (``agents.md §5.7``).

    The six-step tool-loop sequence lives in :class:`ToolUsingAgent` (ADR 0072); this stage supplies
    the capability, output schema, user turn, and output cap, overrides the tool inventory with the
    Planner's producer read set (ADR 0089), and finalizes the framework-derived lab-level
    reproducibility (ADR 0090).
    """

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
        max_output_tokens: int = DEFAULT_PLANNER_MAX_TOKENS,
    ) -> None:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")
        super().__init__(
            provider=provider,
            registry=registry,
            registries=registries,
            agent_label=AgentLabel.PLANNER,
            agent_dir=PLANNER_AGENT_DIR,
            max_tool_iterations=max_tool_iterations,
            nvd_client=nvd_client,
        )
        self._max_output_tokens = max_output_tokens

    def _build_tools_and_executor(self) -> tuple[list[ToolDefinition], ExtractorToolExecutor]:
        """The Planner's producer tool set (ADR 0089): read-only ``external_lookup`` for the slice.

        Overrides the base Extractor inventory so the Planner never advertises a ``propose_*`` tool
        (no value-type proposals — Extractor authority; ``propose_facet`` is Task 7). The executor
        is a :class:`PlannerToolExecutor` (an ``ExtractorToolExecutor`` subtype, so the return type
        holds and reuses the shared ``external_lookup`` engine).
        """
        source_ids = sorted(e.id for e in self._registries.external_data_sources.entries)
        executor = PlannerToolExecutor(registries=self._registries, nvd_client=self._nvd_client)
        return planner_tool_definitions(source_ids), executor

    async def plan(self, attack_spec: AttackSpec, *, preferences: str | None = None) -> PlanResult:
        """Plan one enriched ``attack_spec`` into a draft ``LabManifest`` skeleton.

        ``preferences`` is the user's optional free-form preference blurb (e.g. preferred clouds) —
        **informational**, never a capability gate (``agents.md §5.7``); lab-run-time credentials
        are the generated lab's ``prereqs.pre_lab`` concern, not the Planner's.

        Returns a complete, Layer-1-valid manifest: the LLM emits the structure, then the framework
        overwrites the **lab-level** ``core.reproducibility`` with the value *derived* from the
        AttackSpec's per-step tiers (ADR 0088/0090) — the LLM's value is never authoritative. The
        per-step ``StepBlock.reproducibility`` the LLM emits is content carried forward from the
        AttackSpec unchanged (``§0.7``); the framework does not re-tier it here.
        """
        user_content = self._build_user_turn(attack_spec=attack_spec, preferences=preferences)
        response, executor = await self._emit(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            output_schema=LabManifest,
            user_content=user_content,
            max_tokens=self._max_output_tokens,
        )
        manifest = response.output
        finalized = manifest.model_copy(
            update={
                "core": manifest.core.model_copy(
                    update={"reproducibility": derive_lab_reproducibility(attack_spec)}
                )
            }
        )
        return PlanResult(manifest=finalized, lookups=executor.lookups)

    # --- prompt assembly ---------------------------------------------------

    def _build_user_turn(self, *, attack_spec: AttackSpec, preferences: str | None) -> str:
        digest = build_registry_digest(self._registries)
        digest_block = f"{digest}\n\n" if digest else ""
        prefs_block = (
            f"USER PREFERENCES (informational, not a capability gate):\n{preferences}\n\n"
            if preferences
            else ""
        )
        return (
            "PLANNING TASK — read the enriched AttackSpec below and emit a draft LabManifest "
            "skeleton: phases (with steps but NO implementation.path — no code yet), lab_resources "
            "with lab_role, prereqs split pre_lab/mid_lab, typed inputs/outputs, produces_world_state, "
            "declared facets, and each step's reproducibility carried forward from the AttackSpec "
            "UNCHANGED. Organize the AttackSpec's content; do not invent content, propose value "
            "types, or re-evaluate reproducibility tiers.\n\n"
            f"{prefs_block}"
            f"{digest_block}"
            "ATTACKSPEC (YAML):\n"
            f"{attack_spec.to_yaml()}"
        )


__all__ = [
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "DEFAULT_PLANNER_MAX_TOKENS",
    "PLANNER_AGENT_DIR",
    "Planner",
]
