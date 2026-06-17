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

:meth:`plan` forces a :class:`~cyberlab_gen.agents.planner.outcome.PlanAttempt` (ADR 0092) — a
draft manifest **or** a structured refusal — so the Planner can surface an un-plannable
(``cannot_plan``) or **incoherent** AttackSpec in-band; the framework routes on the returned
``outcome`` (``architecture.md §1.5``), and the Planner never repairs the AttackSpec
(``agents.md §5.7``). On ``planned`` it finalizes the manifest: the **lab-level**
``core.reproducibility`` is **framework-derived** from the AttackSpec's per-step tiers
(``derive_lab_reproducibility``, ADR 0088), never the LLM's value, so ``plan`` overwrites it before
returning (ADR 0090). :meth:`refine` runs the Planner-Jury ``revise`` targeted-patch path (ADR 0054,
generalized over ``SpecEnvelope`` in ADR 0091), re-deriving ``core.reproducibility`` after the patch.
The other framework-owned fields (``spec_version``, ``GenerationBlock.model``) are stamped at the
persist seam (``state/run_persistence.py``), wired in Task 6 — mirroring how the Extractor defers its
own stamps to persistence (ADR 0068/0069). The plan-refinement **coordinator** (the graph that drives
plan↔jury↔refine + route-back) is :mod:`cyberlab_gen.framework.plan_orchestrator`; the ``plan`` verb /
persistence are Task 6.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.agents.extractor.extractor import build_registry_digest
from cyberlab_gen.agents.planner.tools import PlannerToolExecutor, planner_tool_definitions
from cyberlab_gen.agents.results import PlanAttempt, PlanOutcome, PlanResult
from cyberlab_gen.agents.tool_agent import ToolUsingAgent
from cyberlab_gen.errors import PlanningError
from cyberlab_gen.framework.refinement import (
    RefinementPatch,
    RefinementPathError,
    apply_field_patch,
)
from cyberlab_gen.framework.reproducibility import derive_lab_reproducibility
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor.tools import ExtractorToolExecutor
    from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider, ToolDefinition
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.manifest import LabManifest

logger = logging.getLogger(__name__)

PLANNER_AGENT_DIR = "planner"

#: Tool-use loop depth for one Planner pass (mirrors the Extractor; ``provider-interface.md §4.1``).
DEFAULT_MAX_TOOL_ITERATIONS = 12

#: Re-prompt budget for :meth:`Planner.refine` when an emitted ``RefinementPatch`` does not *apply
#: or validate* against the prior manifest (a malformed-output recovery, ``validation.md §6.10.1``
#: mechanism 2 — distinct from the orchestrator-owned refinement-iteration cap). Mirrors the
#: Extractor's ``DEFAULT_PATCH_RETRY_ATTEMPTS``; placeholder per ``architecture.md §8.4``.
DEFAULT_PATCH_RETRY_ATTEMPTS = 2

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
        """Plan one enriched ``attack_spec`` into a draft ``LabManifest`` skeleton — or refuse.

        ``preferences`` is the user's optional free-form preference blurb (e.g. preferred clouds) —
        **informational**, never a capability gate (``agents.md §5.7``); lab-run-time credentials
        are the generated lab's ``prereqs.pre_lab`` concern, not the Planner's.

        The Planner emits a :class:`~cyberlab_gen.agents.planner.outcome.PlanAttempt` (ADR 0092): on
        ``planned`` a complete manifest whose **lab-level** ``core.reproducibility`` the framework
        then overwrites with the value *derived* from the AttackSpec's per-step tiers (ADR 0088/0090,
        the LLM's value is never authoritative); on ``cannot_plan`` / ``attackspec_incoherent`` no
        manifest but a structured ``PlannerRefusal``. The framework reads the returned ``outcome`` to
        route (``architecture.md §1.5``: incoherent → route back to the Extractor; cannot_plan →
        halt) — the Planner never repairs the AttackSpec (``agents.md §5.7``). The per-step
        ``StepBlock.reproducibility`` the LLM emits is content carried forward from the AttackSpec
        unchanged (``§0.7``); the framework does not re-tier it here.
        """
        user_content = self._build_user_turn(attack_spec=attack_spec, preferences=preferences)
        response, executor = await self._emit(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            output_schema=PlanAttempt,
            user_content=user_content,
            max_tokens=self._max_output_tokens,
        )
        attempt = response.output
        if attempt.outcome is not PlanOutcome.PLANNED:
            # The Planner refused; carry the structured detail through for the framework to route on.
            return PlanResult(
                outcome=attempt.outcome, refusal=attempt.refusal, lookups=executor.lookups
            )
        assert attempt.manifest is not None  # the PlanAttempt validator guarantees it for PLANNED
        return PlanResult(
            outcome=PlanOutcome.PLANNED,
            manifest=self._finalize_manifest(attempt.manifest, attack_spec),
            lookups=executor.lookups,
        )

    async def refine(
        self,
        *,
        prior_manifest: LabManifest,
        attack_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        preferences: str | None = None,
    ) -> PlanResult:
        """Re-run the Planner as a *targeted patch* on a Planner-Jury ``revise`` (ADR 0054, 0092).

        Hands the model the prior ``LabManifest`` plus the jury's structured field-level feedback and
        forces a small :class:`~cyberlab_gen.framework.refinement.RefinementPatch` emit covering
        **only** the flagged manifest field paths; the framework deep-sets it onto the prior manifest
        and re-validates the **whole** manifest
        (:func:`~cyberlab_gen.framework.refinement.apply_field_patch`, now generic over
        ``SpecEnvelope``). Refinement is convergent by construction — every unflagged field stays
        byte-identical, so a patch cannot regress a field nobody flagged. The framework-owned
        ``core.reproducibility`` is re-derived after the patch (derive-at-seam, ADR 0090), keeping it
        authoritative on the refine path too — and a patch may not *target* it (rejected by the
        marker-aware path resolver, ADR 0087/0091).

        The only re-prompt loop here recovers from a patch that does not *apply or validate* (R1, a
        malformed-output bound), capped by :data:`DEFAULT_PATCH_RETRY_ATTEMPTS`; on exhaustion it
        raises :class:`~cyberlab_gen.errors.PlanningError` (a clean halt, never an unbounded spin).
        Always returns a ``planned`` ``PlanResult`` — a jury ``revise`` presupposes a plannable
        manifest needing field fixes; the AttackSpec-incoherence route-back is a ``plan``-time
        outcome, not a refine one.
        """
        base_user = self._build_refine_turn(
            prior_manifest=prior_manifest, attack_spec=attack_spec, feedback=feedback
        )
        max_attempts = 1 + DEFAULT_PATCH_RETRY_ATTEMPTS
        extra = ""
        last_problem = "no patch produced"

        for attempt in range(1, max_attempts + 1):
            user_content = base_user if not extra else f"{base_user}\n\n{extra}"
            response, executor = await self._emit(
                capability=CapabilityHint.HIGH_QUALITY_REASONING,
                output_schema=RefinementPatch,
                user_content=user_content,
                max_tokens=self._max_output_tokens,
            )
            try:
                patched = apply_field_patch(prior_manifest, response.output)
            except (RefinementPathError, PydanticValidationError) as exc:
                # A bad path or a mis-shaped/invalid patch: re-prompt with the error, bounded by the
                # patch-apply budget (R1) — never an unbounded retry.
                last_problem = f"patch did not apply/validate: {exc}"
                extra = self._patch_rejected_feedback(str(exc))
                logger.warning(
                    "planner refine: patch did not apply/validate on attempt %d/%d: %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                continue
            return PlanResult(
                outcome=PlanOutcome.PLANNED,
                manifest=self._finalize_manifest(patched, attack_spec),
                lookups=executor.lookups,
                reprompts=attempt - 1,
            )

        raise PlanningError(
            "Planner refine exhausted its patch retry budget "
            f"({max_attempts} attempts); unresolved: {last_problem}"
        )

    # --- framework finalize ------------------------------------------------

    def _finalize_manifest(self, manifest: LabManifest, attack_spec: AttackSpec) -> LabManifest:
        """Overwrite the framework-owned lab-level ``core.reproducibility`` with the derived value.

        The LLM never authors lab-level reproducibility (``schema.md §4.8``, ADR 0088/0090); the
        framework derives it from the AttackSpec's per-step tiers and overwrites whatever the model
        emitted (on ``plan``) or whatever survived the patch merge (on ``refine``) — so the field
        stays authoritative on **both** paths (the "guard every path" lesson, ADR 0085/0087).
        """
        return manifest.model_copy(
            update={
                "core": manifest.core.model_copy(
                    update={"reproducibility": derive_lab_reproducibility(attack_spec)}
                )
            }
        )

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

    def _build_refine_turn(
        self,
        *,
        prior_manifest: LabManifest,
        attack_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
    ) -> str:
        """The refinement user turn: prior manifest + flagged fields + AttackSpec + emit-a-patch."""
        lines: list[str] = []
        for item in feedback:
            line = f"- field_path: {item.field_path}\n  problem: {item.problem}"
            if item.suggested_fix:
                line += f"\n  suggested_fix: {item.suggested_fix}"
            lines.append(line)
        flagged = "\n".join(lines)
        digest = build_registry_digest(self._registries)
        digest_block = f"{digest}\n\n" if digest else ""
        return (
            "REFINEMENT — the Planner-Jury reviewed your prior LabManifest and flagged the fields "
            "below. Emit a RefinementPatch that fixes ONLY these field paths and nothing else. For "
            "each, return a FieldPatch{field_path, new_value} where new_value is the corrected "
            "sub-tree with the SAME shape that path has in the prior manifest — for a content field "
            "that is the whole Provenance object {value, source, citations, ...}. Use dotted paths "
            "with integer list indices (e.g. phases[0].steps[0].description). Re-ground every fix in "
            "the AttackSpec; do not touch any unflagged field, do not propose value types, and do "
            "not re-tier reproducibility (you carry per-step tiers forward unchanged).\n\n"
            f"FLAGGED FIELDS:\n{flagged}\n\n"
            f"{digest_block}"
            "PRIOR LABMANIFEST (YAML):\n"
            f"{prior_manifest.to_yaml()}\n\n"
            "ATTACKSPEC (YAML):\n"
            f"{attack_spec.to_yaml()}"
        )

    def _patch_rejected_feedback(self, detail: str) -> str:
        return (
            "PATCH REJECTED — the previous RefinementPatch did not apply or did not validate. "
            "Re-emit a RefinementPatch addressing only the flagged paths; fix this:\n"
            f"{detail}"
        )


__all__ = [
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "DEFAULT_PATCH_RETRY_ATTEMPTS",
    "DEFAULT_PLANNER_MAX_TOKENS",
    "PLANNER_AGENT_DIR",
    "Planner",
]
