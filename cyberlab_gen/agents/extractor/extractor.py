"""The Extractor stage: produce an AttackSpec from cached blog content.

Architectural source: ``agents.md §5.4``, ``pipeline.md §3.2.2``, ADR 0021.

The Extractor is a typed-output agent (output type ``AttackSpec``) invoked via
Task 2's capability-hint call surface (``long_context_extraction``). After the
provider returns a structurally valid ``AttackSpec``, the *framework* (this
module, never the LLM — ``architecture.md §1.5/§1.6``) runs three mechanical
checks and, on failure, re-prompts the agent with the offending ids/fields
flagged, decrementing a content-level retry budget distinct from the call
surface's structural-malformation budget (ADR 0018, ADR 0021):

1. **search-before-claim** (``schema.md §4.15``) — every ``source: external_api``
   field must have a matching ``external_lookup`` record in the tool trace.
2. **MITRE pass-through** (ADR 0055/0058) — referenced technique ids are accepted
   as-is. A well-formed-but-uncatalogued id is never rejected (the bundled seed is
   not an authority; well-formedness is owned by the ``MitreTechniqueId`` type). This
   check produces no findings until a real MITRE adapter is wired (LATER).
3. **CVE hallucination** — every CVE id whose provenance claims a non-unknown
   source must resolve against NVD (skipped, not failed, when no NVD client is
   wired — the honest "couldn't check" posture).

On budget exhaustion the stage raises ``ExtractionError``. This is *retry*, not
refinement (``architecture.md §1.7``). The stage returns an ``ExtractionResult``
envelope wrapping the validated ``AttackSpec`` plus the collected proposals and
trace; only the ``AttackSpec`` is ever written to disk.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.agents.call_surface import AgentRunner
from cyberlab_gen.agents.extractor.tools import (
    ExternalLookupRecord,
    ExtractorToolExecutor,
    extractor_tool_definitions,
)

# Runtime import (not TYPE_CHECKING): ExtractionResult is a Pydantic model whose
# fields are typed with these, so Pydantic must resolve them at class-definition
# time. ruff's TC001 false-fires under `from __future__ import annotations`.
from cyberlab_gen.agents.proposals import ProposedFacet, ProposedThesisType, ProposedValueType
from cyberlab_gen.errors import ExtractionError
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.enums import ProvenanceSource

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider
    from cyberlab_gen.providers.ranking import ProviderRegistry

logger = logging.getLogger(__name__)

EXTRACTOR_AGENT_DIR = "extractor"

#: Content-level retry budget for hallucination / search-before-claim rejections.
#: Distinct from the call surface's structural-malformation budget (ADR 0018).
#: Placeholder per ``architecture.md §8.4``; calibrated from eval data later.
DEFAULT_HALLUCINATION_RETRY_ATTEMPTS = 2

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


class CheckFinding(InternalModel):
    """One framework-check rejection reason (search-before-claim or hallucination)."""

    kind: str  # "search_before_claim" | "cve_hallucination"
    field_path: str
    detail: str


class ExtractionResult(InternalModel):
    """The Extractor stage's output envelope (ADR 0021).

    Wraps the validated ``AttackSpec`` (the only piece that becomes an artifact)
    plus the side-channel the framework needs downstream: the registry proposals
    the agent emitted, the external-lookup trace (for the jury's independent
    provenance verification), how many content-level re-prompts it took, and the
    framework findings from the final accepted pass (empty on a clean accept).
    """

    attack_spec: AttackSpec
    value_type_proposals: list[ProposedValueType]
    facet_proposals: list[ProposedFacet]
    thesis_type_proposals: list[ProposedThesisType]
    lookups: list[ExternalLookupRecord]
    reprompts: int = 0


class Extractor:
    """Drives one Extractor stage run over cached blog content (``agents.md §5.4``)."""

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: object,  # MergedRegistries; typed loosely to avoid a runtime import here
        nvd_client: NvdClient | None = None,
        hallucination_retry_attempts: int = DEFAULT_HALLUCINATION_RETRY_ATTEMPTS,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
        max_output_tokens: int = DEFAULT_EXTRACTOR_MAX_TOKENS,
    ) -> None:
        if hallucination_retry_attempts < 0:
            raise ValueError("hallucination_retry_attempts must be >= 0")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")
        self._runner = AgentRunner(
            agent_label=AgentLabel.EXTRACTOR,
            agent_dir=EXTRACTOR_AGENT_DIR,
            provider=provider,
            registry=registry,
        )
        self._registries = registries
        self._nvd_client = nvd_client
        self._hallucination_retry_attempts = hallucination_retry_attempts
        self._max_tool_iterations = max_tool_iterations
        self._max_output_tokens = max_output_tokens

    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult:
        """Run the Extractor over ``blog_content``; enforce the framework checks.

        ``source_summary`` is the Ingestion metadata the prompt needs (url,
        publisher, fetched-at, content hash). Returns an ``ExtractionResult`` whose
        ``AttackSpec`` passed search-before-claim + MITRE/CVE hallucination checks,
        or raises ``ExtractionError`` when the content-level retry budget is spent.
        """
        from cyberlab_gen.registries.merge import MergedRegistries

        if not isinstance(self._registries, MergedRegistries):  # pragma: no cover - guard
            raise TypeError("Extractor.registries must be a MergedRegistries")

        source_ids = sorted(e.id for e in self._registries.external_data_sources.entries)
        base_user = self._build_user_turn(blog_content=blog_content, source_summary=source_summary)
        max_attempts = 1 + self._hallucination_retry_attempts
        feedback = ""
        last_findings: list[CheckFinding] = []

        for attempt in range(1, max_attempts + 1):
            executor = ExtractorToolExecutor(
                registries=self._registries, nvd_client=self._nvd_client
            )
            user_content = base_user if not feedback else f"{base_user}\n\n{feedback}"
            messages = self._runner.build_messages(
                capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
                user_content=user_content,
            )
            response = await self._runner.run_with_tools(
                messages,
                output_schema=AttackSpec,
                capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
                tools=extractor_tool_definitions(registered_source_ids=source_ids),
                tool_executor=executor,
                max_iterations=self._max_tool_iterations,
                max_tokens=self._max_output_tokens,
            )
            spec = response.output
            findings = self._run_checks(spec, executor.lookups)
            if not findings:
                return ExtractionResult(
                    attack_spec=spec,
                    value_type_proposals=executor.value_type_proposals,
                    facet_proposals=executor.facet_proposals,
                    thesis_type_proposals=executor.thesis_type_proposals,
                    lookups=executor.lookups,
                    reprompts=attempt - 1,
                )
            last_findings = findings
            feedback = self._feedback_for(findings)
            logger.warning(
                "extractor framework-check rejection on attempt %d/%d: %d finding(s)",
                attempt,
                max_attempts,
                len(findings),
            )

        raise ExtractionError(
            "Extractor exhausted its hallucination/search-before-claim retry budget "
            f"({max_attempts} attempts); unresolved findings: "
            + "; ".join(f"{f.kind}@{f.field_path}: {f.detail}" for f in last_findings)
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
        cannot regress a field nobody flagged (the field-rerolling bounce of full
        re-extraction). Reserved for the jury-``revise`` refinement path; the static-schema
        structural retry and the interactive natural-language-feedback path still re-extract
        from scratch (``architecture.md §1.7``, ``validation.md §6.10``).

        The mechanical content checks (search-before-claim / MITRE / CVE) run over the
        **whole** patched spec, so a patch can't introduce an undetected cross-field problem
        (R2). The re-prompt loop on an unapplyable or framework-rejected patch is bounded by
        the same content-retry budget as :meth:`extract` (R1); on exhaustion it raises
        ``ExtractionError`` (a clean halt, never an unbounded spin).
        """
        # Lazy import: ``cyberlab_gen.framework`` imports this module (the orchestrator needs
        # ``ExtractionResult``), so a top-level framework import here would be a load-time
        # cycle. By call time the framework package is fully initialised. (ADR 0054.)
        from cyberlab_gen.framework.refinement import (
            RefinementPatch,
            RefinementPathError,
            apply_field_patch,
        )
        from cyberlab_gen.registries.merge import MergedRegistries

        if not isinstance(self._registries, MergedRegistries):  # pragma: no cover - guard
            raise TypeError("Extractor.registries must be a MergedRegistries")

        source_ids = sorted(e.id for e in self._registries.external_data_sources.entries)
        base_user = self._build_refine_turn(
            prior_spec=prior_spec,
            feedback=feedback,
            blog_content=blog_content,
            source_summary=source_summary,
        )
        max_attempts = 1 + self._hallucination_retry_attempts
        extra = ""
        last_problem = "no patch produced"

        for attempt in range(1, max_attempts + 1):
            executor = ExtractorToolExecutor(
                registries=self._registries, nvd_client=self._nvd_client
            )
            user_content = base_user if not extra else f"{base_user}\n\n{extra}"
            messages = self._runner.build_messages(
                capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
                user_content=user_content,
            )
            response = await self._runner.run_with_tools(
                messages,
                output_schema=RefinementPatch,
                capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
                tools=extractor_tool_definitions(registered_source_ids=source_ids),
                tool_executor=executor,
                max_iterations=self._max_tool_iterations,
                max_tokens=self._max_output_tokens,
            )
            try:
                patched = apply_field_patch(prior_spec, response.output)
            except (RefinementPathError, PydanticValidationError) as exc:
                # A bad path or a mis-shaped/invalid patch: re-prompt with the error, bounded
                # by the same budget as a content rejection (R1) — never an unbounded retry.
                last_problem = f"patch did not apply/validate: {exc}"
                extra = self._patch_rejected_feedback(str(exc))
                logger.warning(
                    "extractor refine: patch did not apply/validate on attempt %d/%d: %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                continue
            findings = self._run_checks(patched, executor.lookups)  # whole-spec re-check (R2)
            if not findings:
                return ExtractionResult(
                    attack_spec=patched,
                    value_type_proposals=executor.value_type_proposals,
                    facet_proposals=executor.facet_proposals,
                    thesis_type_proposals=executor.thesis_type_proposals,
                    lookups=executor.lookups,
                    reprompts=attempt - 1,
                )
            last_problem = "; ".join(f"{f.kind}@{f.field_path}: {f.detail}" for f in findings)
            extra = self._feedback_for(findings)
            logger.warning(
                "extractor refine: framework-check rejection on attempt %d/%d: %d finding(s)",
                attempt,
                max_attempts,
                len(findings),
            )

        raise ExtractionError(
            "Extractor refine exhausted its patch retry budget "
            f"({max_attempts} attempts); unresolved: {last_problem}"
        )

    # --- prompt assembly ---------------------------------------------------

    def _build_user_turn(self, *, blog_content: str, source_summary: str) -> str:
        return (
            "SOURCE METADATA:\n"
            f"{source_summary}\n\n"
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

    def _feedback_for(self, findings: list[CheckFinding]) -> str:
        lines = ["FRAMEWORK REJECTION — fix these before resubmitting:"]
        for f in findings:
            lines.append(f"- [{f.kind}] {f.field_path}: {f.detail}")
        lines.append(
            "For any external_api value you keep, call external_lookup first. For any "
            "id you cannot ground, set the field to unknown_from_blog with a reason."
        )
        return "\n".join(lines)

    # --- framework checks (mechanical, never LLM — architecture.md §1.6) ---

    def _run_checks(
        self, spec: AttackSpec, lookups: list[ExternalLookupRecord]
    ) -> list[CheckFinding]:
        findings: list[CheckFinding] = []
        findings.extend(self._check_search_before_claim(spec, lookups))
        findings.extend(self._check_mitre(spec))
        findings.extend(self._check_cves(spec, lookups))
        return findings

    def _check_search_before_claim(
        self, spec: AttackSpec, lookups: list[ExternalLookupRecord]
    ) -> list[CheckFinding]:
        """Every ``source: external_api`` field needs a matching tool call (``§4.15``)."""
        findings: list[CheckFinding] = []
        if spec.external_references is None:
            return findings
        looked_up_cves = {
            str(rec.params.get("cve_id", "")).strip() for rec in lookups if rec.source_id == "nvd"
        }
        for cve in spec.external_references.cves:
            for label, prov in (("cvss_score", cve.cvss_score), ("severity", cve.severity)):
                if (
                    prov is not None
                    and prov.source is ProvenanceSource.EXTERNAL_API
                    and cve.cve_id not in looked_up_cves
                ):
                    findings.append(
                        CheckFinding(
                            kind="search_before_claim",
                            field_path=f"external_references.cves[{cve.cve_id}].{label}",
                            detail=(
                                f"claims source=external_api but no external_lookup "
                                f"call recorded for {cve.cve_id}"
                            ),
                        )
                    )
        return findings

    def _check_mitre(self, spec: AttackSpec) -> list[CheckFinding]:
        """MITRE technique ids are never hard-rejected against a local list (ADR 0055/0058).

        Well-formedness is already owned by the ``MitreTechniqueId`` type (``primitives.py``),
        enforced at AttackSpec construction — a malformed id can never reach this check. The
        old 8-entry seed-catalog membership gate rejected real, current ATT&CK ids (e.g. the
        blog-central T1195/T1199) as "hallucinations"; per ADR 0055 P2 an unverifiable-but-
        well-formed identifier must pass THROUGH unverified, never be rejected. Phase 1 wires
        no MITRE verification adapter, so this mirrors ``_check_cves``'s skip-when-unwired
        posture and returns no findings — it only logs which ids went unverified. Verifying
        (and fetching) technique ids via a wired ``external_data_sources/mitre_attack`` adapter
        is the LATER work named in findings doc 0001 §5.
        """
        refs = self._collect_technique_refs(spec)
        if refs:
            logger.info(
                "extractor: %d MITRE technique id(s) passed unverified (no MITRE adapter wired "
                "this phase): %s",
                len(refs),
                ", ".join(tech for _, tech in refs),
            )
        return []

    def _check_cves(
        self, spec: AttackSpec, lookups: list[ExternalLookupRecord]
    ) -> list[CheckFinding]:
        """Every grounded CVE id must resolve against NVD (skipped when no client)."""
        if spec.external_references is None or self._nvd_client is None:
            return []
        findings: list[CheckFinding] = []
        found_cves = {
            str(rec.params.get("cve_id", "")).strip()
            for rec in lookups
            if rec.source_id == "nvd" and rec.found
        }
        for cve in spec.external_references.cves:
            source = cve.description.source
            if source is ProvenanceSource.UNKNOWN_FROM_BLOG:
                continue
            if cve.cve_id not in found_cves:
                findings.append(
                    CheckFinding(
                        kind="cve_hallucination",
                        field_path=f"external_references.cves[{cve.cve_id}]",
                        detail=(
                            f"{cve.cve_id} did not resolve against NVD; a real CVE must be "
                            "confirmed via external_lookup before it is claimed"
                        ),
                    )
                )
        return findings

    def _collect_technique_refs(self, spec: AttackSpec) -> list[tuple[str, str]]:
        """Gather (field_path, technique_id) for every MITRE reference in the spec."""
        out: list[tuple[str, str]] = []
        if spec.chain is not None:
            for step in spec.chain.chain_steps:
                for tech in step.techniques.mitre:
                    out.append((f"chain.chain_steps[{step.id}].techniques.mitre", tech))
        if spec.external_references is not None:
            for ref in spec.external_references.mitre_techniques:
                out.append(
                    (
                        f"external_references.mitre_techniques[{ref.technique_id}]",
                        ref.technique_id,
                    )
                )
        return out


__all__ = [
    "DEFAULT_EXTRACTOR_MAX_TOKENS",
    "DEFAULT_HALLUCINATION_RETRY_ATTEMPTS",
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "EXTRACTOR_AGENT_DIR",
    "CheckFinding",
    "ExtractionResult",
    "Extractor",
]
