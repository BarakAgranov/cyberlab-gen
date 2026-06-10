"""The Extractor stage: produce an AttackSpec from cached blog content.

Architectural source: ``agents.md §5.4``, ``pipeline.md §3.2.2``, ADR 0021,
**ADR 0051/0060** (the Extractor stops self-validating).

The Extractor is a typed-output agent (output type ``AttackSpec``) invoked via
Task 2's capability-hint call surface (``long_context_extraction``). It **produces
content** and nothing more: it returns a structurally valid ``AttackSpec`` in one
pass. The mechanical grounding checks (search-before-claim / MITRE / CVE) that this
stage used to run on its own hidden ``hallucination_retry`` budget now live in the
**orchestrator-owned grounding stack** (``validation.md §6.10.2``,
:mod:`cyberlab_gen.validators.grounding_validator`); the *orchestrator* — never this
LLM-producing stage — owns the route and the retry budget (``architecture.md
§1.5``: LLMs never decide their own retry budgets).

:meth:`extract` runs once and returns an ``ExtractionResult`` envelope wrapping the
validated ``AttackSpec`` plus the side-channel the framework needs downstream (the
registry proposals and the external-lookup trace the grounding stack consumes); only
the ``AttackSpec`` is ever written to disk.

:meth:`refine` (the jury-``revise`` targeted-patch path, ADR 0048/0054) keeps a small,
bounded re-prompt loop for one narrow concern only — a patch that does not *apply or
validate* (a malformed-output recovery, ``validation.md §6.10.1`` mechanism 2). The
mechanical grounding re-check of the patched spec (R2) is no longer run here; the
patched spec re-enters the orchestrator's static-schema + grounding stack on the graph,
so the whole-spec re-check is preserved without a hidden Extractor loop (ADR 0051/0060).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.agents.results import ExtractionResult
from cyberlab_gen.agents.tool_agent import ToolUsingAgent
from cyberlab_gen.errors import ExtractionError
from cyberlab_gen.framework.refinement import (
    RefinementPatch,
    RefinementPathError,
    apply_field_patch,
)
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries

logger = logging.getLogger(__name__)

EXTRACTOR_AGENT_DIR = "extractor"

#: Re-prompt budget for :meth:`Extractor.refine` when an emitted ``RefinementPatch``
#: does not *apply or validate* (a malformed-output recovery, ``validation.md §6.10.1``
#: mechanism 2 — distinct from the orchestrator-owned grounding-retry budget, ADR
#: 0051/0060). It is NOT a content/hallucination retry; the grounding re-check of the
#: patched spec is the orchestrator's job. Placeholder per ``architecture.md §8.4``.
DEFAULT_PATCH_RETRY_ATTEMPTS = 2

#: Tool-use loop depth for one Extractor pass (``provider-interface.md §4.1``).
DEFAULT_MAX_TOOL_ITERATIONS = 12

#: Output-token budget for the AttackSpec emit (ADR 0032, recalibrated by
#: ``dev/investigations/0002``). The Extractor emits the *entire* AttackSpec as one tool
#: call; the provider default (``DEFAULT_MAX_TOKENS`` = 4096) truncates it mid-emit on any
#: non-trivial chain — the bug ADR 0032 diagnosed. The model ceiling for
#: ``claude-opus-4-8`` is 128K output tokens, but the provider call is **non-streaming**,
#: and the Anthropic SDK refuses a non-streaming request whose estimated time exceeds 10
#: minutes — i.e. ``max_tokens`` above ``600/3600 * 128000 ≈ 21_333`` raises ``ValueError``
#: (``anthropic._base_client._calculate_nonstreaming_timeout``).
#:
#: **Recalibration (investigation 0002).** ADR 0032's original note — "16384 covers a
#: realistically rich spec with margin; a measured 9-step spec serialises to ~12K output
#: tokens" — was **falsified** by a real Sysdig run: an 8-step spec serialised to ~16K
#: output tokens and truncated at the 16384 ceiling. 20000 gives the dense tail ~30%
#: headroom while staying below the ~21_333 non-streaming wall. This is a **stopgap, not
#: the truncation class-fix**: a spec above ~20K still truncates with no recourse
#: (``chain_steps`` has no schema maximum). Closing the class requires converting the tool
#: loop to streaming (which removes the non-streaming wall) + a sectioned/continuation
#: emit — the deferred D1/D2 work, neither of which exists yet (``implementation-plan.md
#: §4.6``; investigation 0002 §5).
DEFAULT_EXTRACTOR_MAX_TOKENS = 20000


class Extractor(ToolUsingAgent):
    """Drives one Extractor stage run over cached blog content (``agents.md §5.4``).

    The six-step tool-loop sequence lives in :class:`ToolUsingAgent` (ADR 0072); this stage supplies
    the capability, output schema, user turn, and output cap, and wraps the typed result.
    """

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
        max_output_tokens: int = DEFAULT_EXTRACTOR_MAX_TOKENS,
    ) -> None:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")
        super().__init__(
            provider=provider,
            registry=registry,
            registries=registries,
            agent_label=AgentLabel.EXTRACTOR,
            agent_dir=EXTRACTOR_AGENT_DIR,
            max_tool_iterations=max_tool_iterations,
            nvd_client=nvd_client,
        )
        self._max_output_tokens = max_output_tokens

    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult:
        """Run the Extractor over ``blog_content`` once and return its output envelope.

        ``source_summary`` is the Ingestion metadata the prompt needs (url, publisher,
        fetched-at, content hash) — plus, on an orchestrator-routed retry, the structural
        or grounding findings folded in by the orchestrator (``architecture.md §1.5``).
        The Extractor produces content and does **not** self-validate; the orchestrator's
        static-schema + grounding stack checks the output and decides any re-run.
        """
        user_content = self._build_user_turn(
            blog_content=blog_content, source_summary=source_summary
        )
        response, executor = await self._emit(
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            output_schema=AttackSpec,
            user_content=user_content,
            max_tokens=self._max_output_tokens,
        )
        return ExtractionResult(
            attack_spec=response.output,
            value_type_proposals=executor.value_type_proposals,
            facet_proposals=executor.facet_proposals,
            thesis_type_proposals=executor.thesis_type_proposals,
            lookups=executor.lookups,
            reprompts=0,
        )

    async def refine(
        self,
        *,
        prior_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        blog_content: str,
        source_summary: str,
    ) -> ExtractionResult:
        """Re-run the Extractor as a *targeted patch* on a jury ``revise`` (ADR 0048 A1, ADR 0054).

        Hands the model the prior ``AttackSpec`` plus the jury's structured field-level
        feedback and forces a small :class:`~cyberlab_gen.framework.refinement.RefinementPatch`
        emit covering **only** the flagged field paths; the framework deep-sets it onto the
        prior spec and re-validates the **whole** spec
        (:func:`~cyberlab_gen.framework.refinement.apply_field_patch`). Refinement is
        convergent by construction — every unflagged field stays byte-identical, so a patch
        cannot regress a field nobody flagged.

        The only re-prompt loop here recovers from a patch that does not *apply or validate*
        (R1, a malformed-output bound), capped by :data:`DEFAULT_PATCH_RETRY_ATTEMPTS`; on
        exhaustion it raises ``ExtractionError`` (a clean halt, never an unbounded spin). The
        mechanical grounding re-check of the patched spec (R2) is **not** run here — the
        patched spec re-enters the orchestrator's static-schema + grounding stack on the
        graph, so the whole-spec re-check is preserved without a hidden Extractor loop
        (ADR 0051/0060).
        """
        base_user = self._build_refine_turn(
            prior_spec=prior_spec,
            feedback=feedback,
            blog_content=blog_content,
            source_summary=source_summary,
        )
        max_attempts = 1 + DEFAULT_PATCH_RETRY_ATTEMPTS
        extra = ""
        last_problem = "no patch produced"

        for attempt in range(1, max_attempts + 1):
            user_content = base_user if not extra else f"{base_user}\n\n{extra}"
            response, executor = await self._emit(
                capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
                output_schema=RefinementPatch,
                user_content=user_content,
                max_tokens=self._max_output_tokens,
            )
            try:
                patched = apply_field_patch(prior_spec, response.output)
            except (RefinementPathError, PydanticValidationError) as exc:
                # A bad path or a mis-shaped/invalid patch: re-prompt with the error, bounded
                # by the patch-apply budget (R1) — never an unbounded retry.
                last_problem = f"patch did not apply/validate: {exc}"
                extra = self._patch_rejected_feedback(str(exc))
                logger.warning(
                    "extractor refine: patch did not apply/validate on attempt %d/%d: %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                continue
            return ExtractionResult(
                attack_spec=patched,
                value_type_proposals=executor.value_type_proposals,
                facet_proposals=executor.facet_proposals,
                thesis_type_proposals=executor.thesis_type_proposals,
                lookups=executor.lookups,
                reprompts=attempt - 1,
            )

        raise ExtractionError(
            "Extractor refine exhausted its patch retry budget "
            f"({max_attempts} attempts); unresolved: {last_problem}"
        )

    # --- prompt assembly ---------------------------------------------------

    def _build_user_turn(self, *, blog_content: str, source_summary: str) -> str:
        digest = build_registry_digest(self._registries)
        digest_block = f"{digest}\n\n" if digest else ""
        return (
            "SOURCE METADATA:\n"
            f"{source_summary}\n\n"
            f"{digest_block}"
            "BLOG CONTENT (verbatim; cite passages by quoting them in blog_excerpt):\n"
            f"{blog_content}"
        )

    def _build_refine_turn(
        self,
        *,
        prior_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        blog_content: str,
        source_summary: str,
    ) -> str:
        """The refinement user turn: prior spec + flagged fields + emit-a-RefinementPatch."""
        lines: list[str] = []
        for item in feedback:
            line = f"- field_path: {item.field_path}\n  problem: {item.problem}"
            if item.suggested_fix:
                line += f"\n  suggested_fix: {item.suggested_fix}"
            lines.append(line)
        flagged = "\n".join(lines)
        return (
            "REFINEMENT — the Extractor-Jury reviewed your prior AttackSpec and flagged the "
            "fields below. Emit a RefinementPatch that fixes ONLY these field paths and "
            "nothing else. For each, return a FieldPatch{field_path, new_value} where "
            "new_value is the corrected sub-tree with the SAME shape that path has in the "
            "prior spec — for a content field that is the whole Provenance object "
            "{value, source, citations, ...}. Use dotted paths with integer list indices "
            "(e.g. chain.chain_steps[0].description). Re-ground every fix in the blog; do "
            "not touch any unflagged field.\n\n"
            f"FLAGGED FIELDS:\n{flagged}\n\n"
            "PRIOR ATTACKSPEC (YAML):\n"
            f"{prior_spec.to_yaml()}\n\n"
            "SOURCE METADATA:\n"
            f"{source_summary}\n\n"
            "BLOG CONTENT (verbatim; cite passages by quoting them):\n"
            f"{blog_content}"
        )

    def _patch_rejected_feedback(self, detail: str) -> str:
        return (
            "PATCH REJECTED — the previous RefinementPatch did not apply or did not validate. "
            "Re-emit a RefinementPatch addressing only the flagged paths; fix this:\n"
            f"{detail}"
        )


def build_registry_digest(registries: MergedRegistries) -> str:
    """Render a compact digest of the registered vocabulary the Extractor may reference (E1).

    Names only — ``value_types`` / ``facets`` / ``thesis_types`` / ``execution_contexts`` — so
    the Extractor can check novelty before proposing rather than proposing blind (which forces a
    structural-retry re-extraction; investigation 0002 §6 / 0001, ADR 0050/0062). NOT
    ``external_data_sources`` (a tool-adapter catalog, never LLM-proposable; ADR 0055/0058).
    Bounded to entry names (no ``value_schema`` bodies) for token cost.
    """

    def _fmt(names: list[str]) -> str:
        return ", ".join(names) if names else "(none)"

    value_types = sorted(e.name for e in registries.value_types.entries)
    facets = sorted(e.name for e in registries.facets.entries)
    thesis_types = sorted(e.name for e in registries.thesis_types.entries)
    execution_contexts = sorted(e.name for e in registries.execution_contexts.entries)
    return (
        "REGISTRY DIGEST (vocabulary already registered — use these as-is; propose ONLY "
        "genuinely-novel terms NOT listed here):\n"
        f"- value_types: {_fmt(value_types)}\n"
        f"- facets: {_fmt(facets)}\n"
        f"- thesis_types: {_fmt(thesis_types)}\n"
        f"- execution_contexts: {_fmt(execution_contexts)}"
    )


__all__ = [
    "DEFAULT_EXTRACTOR_MAX_TOKENS",
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "DEFAULT_PATCH_RETRY_ATTEMPTS",
    "EXTRACTOR_AGENT_DIR",
    "ExtractionResult",
    "Extractor",
    "build_registry_digest",
]
