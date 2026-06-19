"""The orchestrator-owned grounding / search-before-claim mechanical-validator stack.

Architectural source: ``validation.md §6.10.2`` ("One orchestrator-owned
mechanical-validator stack"), ``validation.md §6.10.1`` (grounding /
search-before-claim is a *retry* mechanism, owned by the orchestrator's stack —
not the producing agent), ``agents.md §5.4``/``§5.5``, ADR 0051, ADR 0060.

This module is the **relocation** of two formerly-scattered, partly-duplicated
mechanical checks into ONE place the orchestrator owns and routes:

* the Extractor's former internal ``_run_checks`` loop (search-before-claim,
  MITRE pass-through, CVE-hallucination), which used to run inside the Extractor
  stage on the Extractor's own hidden ``hallucination_retry`` budget; and
* the jury's former ``verify_provenance`` (the per-source provenance-structure
  walk and the external-API-trace cross-check) — the trace check was a
  near-verbatim duplicate of the Extractor's search-before-claim check
  (``architecture.md §1.5``: an LLM-producing stage must not own its own
  framework-check retry budget, and one mechanical check must not be implemented
  twice).

The validator is framework code: **no LLM, no network** (``architecture.md
§1.6``). It never mutates the spec and never routes; it returns a
``GroundingResult`` of findings and the orchestrator decides what to do with them
(``architecture.md §1.5``). The jury *consumes* this findings set and adds only
the semantic judgment it is uniquely for (``agents.md §5.5``); it does not
re-derive these findings.

The three sibling layers it produces (``validation.md §6.10.2``):

1. **Provenance structure** — every content field's provenance envelope is
   well-formed for its source kind (``schema.md §4.9``). Informational jury
   grounding: a structure problem is fed to the jury, not auto-retried.
2. **Grounding / search-before-claim** — every *agent-claimed* ``external_api``
   field has matching ``external_lookup`` trace evidence (``schema.md §4.15``).
   A failure here is a hallucination: it is **retry-triggering** (the orchestrator
   re-runs the Extractor).
3. **CVE-hallucination** — every grounded CVE id resolves against NVD (skipped,
   not failed, when no NVD client is wired — the honest "couldn't check" posture).
   Retry-triggering when it fires.

MITRE technique ids are accepted as-is: a well-formed-but-uncatalogued id passes
**unverified**, never a finding (ADR 0055/0058 P2 — the bundled seed is not an
authority; well-formedness is owned by the ``MitreTechniqueId`` type). This
mirrors the post-ADR-0058 Extractor behaviour verbatim through the relocation.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from cyberlab_gen.external_data_sources.types import CveResolution
from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource
from cyberlab_gen.schemas.provenance import Provenance
from cyberlab_gen.validators.base import Finding, FindingResult

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
    from cyberlab_gen.schemas.attack_spec import AttackSpec

logger = logging.getLogger(__name__)

_NVD_SOURCE_ID = "nvd"


class GroundingCode(StrEnum):
    """The kinds of grounding violation the stack can report (``validation.md §6.10.2``)."""

    PROVENANCE_STRUCTURE = "provenance_structure"
    SEARCH_BEFORE_CLAIM = "search_before_claim"
    CVE_HALLUCINATION = "cve_hallucination"
    SOURCE_OF_RECORD_UNKNOWN = "source_of_record_unknown"


#: Finding codes that trigger an orchestrator-owned re-extract *retry* (a hallucination
#: the producing agent must fix), as opposed to informational jury grounding. Per
#: ``validation.md §6.10.1`` grounding/search-before-claim is the *retry* mechanism;
#: a provenance-structure problem is jury grounding (the jury consumes it, ``§6.10.2``).
_RETRY_CODES = frozenset({GroundingCode.SEARCH_BEFORE_CLAIM, GroundingCode.CVE_HALLUCINATION})


class GroundingFinding(Finding[GroundingCode]):
    """One grounding violation: a code, a field locator, and a human-readable detail.

    Shares the ``(code, location, detail)`` shape + ``render()`` with every mechanical-validator
    finding (ADR 0073); ``location`` uses the JSONPath-like convention shared with the static-schema
    findings so a retry can target the offending field.
    """


class GroundingResult(FindingResult[GroundingFinding]):
    """The grounding stack's findings set (``validation.md §6.10.2`` "one findings set").

    Inherits ``findings`` + ``rendered_findings()`` from :class:`FindingResult`; adds the
    grounding-layer retry view.
    """

    @property
    def needs_retry(self) -> bool:
        """True when any finding is a hallucination the orchestrator must re-extract for."""
        return any(f.code in _RETRY_CODES for f in self.findings)

    def retry_findings(self) -> list[GroundingFinding]:
        """The retry-triggering subset (search-before-claim / CVE-hallucination)."""
        return [f for f in self.findings if f.code in _RETRY_CODES]


class GroundingValidator:
    """Runs the orchestrator-owned grounding stack over an ``AttackSpec`` (ADR 0051/0060).

    The validator is **no-network** (``architecture.md §1.6``): the CVE ship-gate
    verifies grounded CVE ids against NVD by *consuming the per-CVE outcomes the
    pre-Planner enrichment pass already produced* (``cve_resolution``, passed to
    :meth:`validate`) — enrichment is the network pass, the validator never calls
    out. No ``cve_resolution`` (the hermetic default, or no NVD client wired to
    enrichment) → the gate is skipped, not failed — the honest "couldn't check"
    posture (ADR 0101, refining ADR 0077). ``known_source_ids`` (the registered
    ``external_data_sources`` ids) enables the post-enrichment ``source_of_record``
    membership check; ``None`` skips it. Stateless across calls; never mutates inputs.
    """

    def __init__(self, *, known_source_ids: frozenset[str] | None = None) -> None:
        self._known_source_ids = known_source_ids

    def validate(
        self,
        spec: AttackSpec,
        lookups: list[ExternalLookupRecord],
        *,
        cve_resolution: dict[str, CveResolution] | None = None,
    ) -> GroundingResult:
        """Run the sibling layers + MITRE pass-through; return one findings set.

        ``lookups`` is the Extractor's external-lookup trace (for the search-before-claim
        cross-check). ``cve_resolution`` is enrichment's per-CVE NVD outcome (for the
        ship-gate). Never raises on a grounding problem — those are findings; the
        orchestrator decides routing (``architecture.md §1.5``).
        """
        findings: list[GroundingFinding] = []
        findings.extend(self._check_provenance_structure(spec))
        findings.extend(self._check_search_before_claim(spec, lookups))
        self._log_mitre(spec)  # MITRE: pass-through, no findings (ADR 0055/0058)
        findings.extend(self._check_cves(spec, cve_resolution))
        findings.extend(self._check_source_of_record(spec))
        if findings:
            logger.info("grounding stack produced %d finding(s)", len(findings))
        return GroundingResult(findings=findings)

    # --- layer 1: provenance structure -------------------------------------

    def _check_provenance_structure(self, spec: AttackSpec) -> list[GroundingFinding]:
        """Per-source-kind well-formedness of every provenance envelope (``schema.md §4.9``).

        Walks the whole spec and checks the structure each source kind demands (a
        ``blog_explicit`` field needs a blog citation, an ``external_api`` field needs an
        ``external_api_response`` citation, etc.). The ``Provenance`` model's own validator
        enforces some of these at construction; this walk re-checks across the whole spec so
        a structurally valid envelope with the wrong *kind* of citation is still caught.
        """
        findings: list[GroundingFinding] = []
        for path, prov in _iter_provenance(spec):
            detail = _structure_problem(prov)
            if detail is not None:
                findings.append(
                    GroundingFinding(
                        code=GroundingCode.PROVENANCE_STRUCTURE, location=path, detail=detail
                    )
                )
        return findings

    # --- layer 2: grounding / search-before-claim --------------------------

    def _check_search_before_claim(
        self, spec: AttackSpec, lookups: list[ExternalLookupRecord]
    ) -> list[GroundingFinding]:
        """Every *agent-claimed* ``external_api`` CVE field needs a matching trace call.

        The single, de-duplicated trace cross-check (formerly in both the Extractor's
        ``_check_search_before_claim`` and the jury's ``_check_api_trace``). It needs the
        CVE id, which the generic provenance walker loses (it uses list indices), so it
        runs explicitly against the CVE refs.

        **Framework-enriched fields are exempt** (ADR 0052 / 0061): a ``framework_enriched``
        ``external_api`` field is the framework's own authoritative NVD call — the API-response
        citation IS the evidence, and the call is not (and need not be) in the agent's lookup
        trace. Only *agent-claimed* ``external_api`` fields are held to search-before-claim.
        """
        findings: list[GroundingFinding] = []
        if spec.external_references is None:
            return findings
        looked_up = {
            str(rec.params.get("cve_id", "")).strip()
            for rec in lookups
            if rec.source_id == _NVD_SOURCE_ID
        }
        for i, cve in enumerate(spec.external_references.cves):
            for label, prov in (("cvss_score", cve.cvss_score), ("severity", cve.severity)):
                if (
                    prov is not None
                    and prov.source is ProvenanceSource.EXTERNAL_API
                    and not prov.framework_enriched
                    and cve.cve_id not in looked_up
                ):
                    findings.append(
                        GroundingFinding(
                            code=GroundingCode.SEARCH_BEFORE_CLAIM,
                            # Integer list index (ADR 0074), so the locator can feed a targeted
                            # patch; the cve id is named in the detail below.
                            location=f"external_references.cves[{i}].{label}",
                            detail=(
                                f"claims source=external_api but no external_lookup call "
                                f"recorded for {cve.cve_id} in the trace"
                            ),
                        )
                    )
        return findings

    # --- MITRE pass-through (no findings; ADR 0055/0058) -------------------

    def _log_mitre(self, spec: AttackSpec) -> None:
        """Log which MITRE ids went unverified; never produce a finding (ADR 0055/0058 P2).

        Well-formedness is owned by ``MitreTechniqueId`` (enforced at AttackSpec
        construction), so a malformed id can never reach here. An unverifiable-but-
        well-formed id passes THROUGH unverified — never rejected against the 8-entry
        seed, which would mislabel real, current ATT&CK ids (T1195/T1199/…) as
        hallucinations. Verifying via a wired MITRE adapter is LATER work (findings 0001 §5).
        """
        refs = _collect_technique_refs(spec)
        if refs:
            logger.info(
                "grounding: %d MITRE technique id(s) passed unverified (no MITRE adapter wired "
                "this phase): %s",
                len(refs),
                ", ".join(tech for _, tech in refs),
            )

    # --- layer 3: CVE-hallucination ----------------------------------------

    def _check_cves(
        self, spec: AttackSpec, cve_resolution: dict[str, CveResolution] | None
    ) -> list[GroundingFinding]:
        """Every grounded CVE id must resolve against NVD (ship-gate, un-inerted via ADR 0101).

        Consumes ``cve_resolution`` — the per-CVE outcome the framework's enrichment NVD call
        already produced — so the gate is real **without the validator doing its own network
        I/O** (``architecture.md §1.6``). A grounded (non-``unknown_from_blog``) CVE that NVD
        has no record of (``ABSENT``) is a hallucination → retry-triggering. ``CONFIRMED`` /
        ``UNAVAILABLE`` (NVD down/rate-limited — never penalise the agent, ADR 0042) / a CVE
        absent from the map (never NVD-checked) produce no finding — the honest "couldn't check".
        """
        if spec.external_references is None or cve_resolution is None:
            return []
        findings: list[GroundingFinding] = []
        for i, cve in enumerate(spec.external_references.cves):
            if cve.description.source is ProvenanceSource.UNKNOWN_FROM_BLOG:
                continue
            if cve_resolution.get(cve.cve_id) is CveResolution.ABSENT:
                findings.append(
                    GroundingFinding(
                        code=GroundingCode.CVE_HALLUCINATION,
                        # Integer list index (ADR 0074); the cve id is named in the detail.
                        location=f"external_references.cves[{i}]",
                        detail=(
                            f"{cve.cve_id} did not resolve against NVD during enrichment; a real "
                            "CVE must be confirmed before it is claimed"
                        ),
                    )
                )
        return findings

    # --- layer 4: source_of_record membership (post-enrichment) ------------

    def _check_source_of_record(self, spec: AttackSpec) -> list[GroundingFinding]:
        """Every framework-set ``cve.source_of_record`` must resolve to a registered source.

        Post-enrichment, mechanical, no-network (ADR 0077 / 0101). ``source_of_record`` is
        framework-authored by enrichment to a real registry id by construction, so this is a
        **defensive guard** against a loaded/hand-edited spec carrying a source id that resolves
        in no registry. Informational (never retry-triggering): a re-extract cannot fix a
        framework-owned field. Skipped when no ``known_source_ids`` was supplied (couldn't check).
        """
        if self._known_source_ids is None or spec.external_references is None:
            return []
        findings: list[GroundingFinding] = []
        for i, cve in enumerate(spec.external_references.cves):
            sor = cve.source_of_record
            if sor is not None and sor not in self._known_source_ids:
                findings.append(
                    GroundingFinding(
                        code=GroundingCode.SOURCE_OF_RECORD_UNKNOWN,
                        location=f"external_references.cves[{i}].source_of_record",
                        detail=(
                            f"source_of_record {sor!r} resolves to no registered "
                            "external_data_sources id"
                        ),
                    )
                )
        return findings


# --- module-level helpers (relocated from the former verification.py) ------


def _structure_problem(prov: Provenance[object]) -> str | None:
    """Return the structure-mismatch detail for one provenance envelope, or ``None``."""
    src = prov.source
    has_blog = any(c.kind is CitationKind.BLOG_PASSAGE for c in prov.citations)
    has_api = any(c.kind is CitationKind.EXTERNAL_API_RESPONSE for c in prov.citations)

    if src is ProvenanceSource.BLOG_EXPLICIT and not has_blog:
        return "blog_explicit field lacks a blog_passage citation"
    if src is ProvenanceSource.EXTERNAL_API and not has_api:
        return "external_api field lacks an external_api_response citation"
    if src is ProvenanceSource.LLM_INFERENCE:
        if prov.confidence is None:
            return "llm_inference field lacks a confidence"
        if not prov.citations:
            return "llm_inference field lacks any citation"
    if src is ProvenanceSource.UNKNOWN_FROM_BLOG:
        if not prov.reason:
            return "unknown_from_blog field lacks a reason"
        if prov.citations:
            return "unknown_from_blog field must not carry citations"
    return None


def _iter_provenance(value: object, path: str = "") -> list[tuple[str, Provenance[object]]]:
    """Depth-first walk yielding ``(field_path, Provenance)`` for every envelope.

    Recurses through Pydantic models (via ``__dict__``) and lists. ``Provenance``
    instances are yielded and not descended into (their ``value`` may itself be a
    model, but the provenance contract is about the envelope, not nested content).
    """
    out: list[tuple[str, Provenance[object]]] = []
    if isinstance(value, Provenance):
        out.append((path or "<root>", cast("Provenance[object]", value)))
        return out
    if isinstance(value, BaseModel):
        fields = cast("dict[str, object]", value.__dict__)
        for name, field_value in fields.items():
            child_path = f"{path}.{name}" if path else name
            out.extend(_iter_provenance(field_value, child_path))
        return out
    if isinstance(value, list):
        items = cast("list[object]", value)
        for idx, item in enumerate(items):
            out.extend(_iter_provenance(item, f"{path}[{idx}]"))
        return out
    return out


def _collect_technique_refs(spec: AttackSpec) -> list[tuple[str, str]]:
    """Gather ``(field_path, technique_id)`` for every MITRE reference in the spec."""
    out: list[tuple[str, str]] = []
    if spec.chain is not None:
        for step in spec.chain.chain_steps:
            for tech in step.techniques.mitre:
                out.append((f"chain.chain_steps[{step.id}].techniques.mitre", tech))
    if spec.external_references is not None:
        for ref in spec.external_references.mitre_techniques:
            out.append(
                (f"external_references.mitre_techniques[{ref.technique_id}]", ref.technique_id)
            )
    return out


__all__ = [
    "GroundingCode",
    "GroundingFinding",
    "GroundingResult",
    "GroundingValidator",
]
