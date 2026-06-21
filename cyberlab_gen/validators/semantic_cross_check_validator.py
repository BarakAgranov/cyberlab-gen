"""The semantic cross-check validator — cross-block consistency within a ``LabManifest``.

Architectural source: ``validation.md §6.5`` (semantic cross-check — what it checks, read-only /
findings-not-mutation), ``§6.10``/``§6.10.2`` (findings route to the responsible agent; the
orchestrator owns the stack), ``architecture.md §1.6`` (mechanical, never LLM). Subclasses the shared
``Finding``/``FindingResult`` base (ADR 0073); locators are integer-indexed (ADR 0074). Scope and the
two surfaced doc gaps are recorded in ADR 0094.

This is the **second** mechanical validation layer; per ADR 0046 / ``coding-conventions.md §5.5`` it
carries the descriptive name from its ``§6.5`` title ("semantic cross-check"), never an ordinal
token. It runs deterministic checks — **no LLM, no network**.

**In Phase 2 the cross-block-within-manifest checks are live:**

1. **Facet ``implies``** — for each declared facet, every facet its registry entry ``implies`` must
   also be declared (``schema.md §4.13``); a missing one is a finding routed to the Planner, which
   adds it next iteration. The Validator **never** adds it — it stays read-only.
2. **Facet ``incompatible_with``** — a declared pair where either side lists the other is a finding
   (symmetric; each contradictory pair reported once).
3. **``produces_world_state`` ``identifier_source`` resolution** — every ``runtime_generated`` entry
   must carry an ``identifier_source`` of the documented form ``phase_outputs.<name>`` resolving to a
   declared ``outputs[].name`` on that phase (``schema.md §4.5``, ``§6.5``); otherwise cleanup code
   would read a source that never exists.

**The code-vs-manifest checks are inert until Phase 3** (they need generated IaC/code that does not
exist yet): the ``references_lab_outputs`` bidirectional cross-check is built as
:func:`references_lab_outputs_findings` (returns nothing this phase; Phase 3 supplies the generated
reference set and wires it). **There is no ``affected_platforms`` consistency check — it is moot by
design** (not deferred): platforms are facet-derived (``schema.md §4.4``) and validated at the
static-schema validation pass via registry membership; they *are* the ``target:*`` facets, not a separate field. ``CoreBlock``
carries no ``affected_platforms`` field (and is ``extra="forbid"``), so there is no independent
operand to cross-check and no reserved code (ADR 0094 D4 → ADR 0095; ``validation.md §6.5``
reconciled).

The validator **never mutates** the manifest and **never routes**: it returns a
``SemanticCrossCheckResult`` of findings, and the orchestrator decides what to do
(``architecture.md §1.5``). The finding→responsible-agent mapping the coordinator consumes is the
module-level :func:`responsible_agent_for` (Task 6 wires the graph node); in Phase 2 every live
finding routes to the Planner.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from cyberlab_gen.schemas.enums import IdentifierKind
from cyberlab_gen.validators.base import Finding, FindingResult

if TYPE_CHECKING:
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.manifest import LabManifest

logger = logging.getLogger(__name__)

#: The documented prefix of a ``runtime_generated`` ``identifier_source`` (``schema.md §4.5``):
#: ``phase_outputs.<name>`` where ``<name>`` is a declared phase output key.
_PHASE_OUTPUTS_PREFIX = "phase_outputs."


class SemanticCrossCheckCode(StrEnum):
    """The kinds of cross-block inconsistency this layer can report (``validation.md §6.5``)."""

    # --- live in Phase 2 (cross-block-within-manifest) ---
    MISSING_IMPLIED_FACET = "missing_implied_facet"
    INCOMPATIBLE_FACETS = "incompatible_facets"
    UNRESOLVED_IDENTIFIER_SOURCE = "unresolved_identifier_source"
    # --- reserved for Phase 3 (code-vs-manifest; see references_lab_outputs_findings) ---
    #: per-phase IaC references a lab-level output the Lab-level Generator never produced.
    UNDECLARED_LAB_OUTPUT_REFERENCE = "undeclared_lab_output_reference"
    #: per-phase IaC references a ``lab_resources`` entry the Planner did not declare.
    UNDECLARED_LAB_RESOURCE_REFERENCE = "undeclared_lab_resource_reference"


class SemanticCrossCheckFinding(Finding[SemanticCrossCheckCode]):
    """One semantic cross-check violation: a code, a field locator, and a human-readable detail.

    Shares the ``(code, location, detail)`` shape + ``render()`` with every mechanical-validator
    finding (ADR 0073); ``location`` uses the integer-indexed JSONPath-like convention (ADR 0074) so
    a finding can feed a targeted re-run / patch.
    """


class SemanticCrossCheckResult(FindingResult[SemanticCrossCheckFinding]):
    """The semantic cross-check verdict: a pass/fail plus the findings list (``validation.md §6.9``).

    Inherits ``findings`` + ``rendered_findings()`` from :class:`FindingResult`; adds the layer's
    ``passed`` flag (``passed`` ⇔ no findings).
    """

    passed: bool


# --- routing seam (consumed by the Task-6 plan coordinator) ----------------


class ResponsibleAgent(StrEnum):
    """The agent the refinement coordinator re-runs for a finding (``validation.md §6.5``/``§6.10``).

    Phase 2 has only the Planner; the code-vs-manifest checks that route to the Phase-3 Generators
    are inert, so their agents are not enumerated until Phase 3 lights those checks up.
    """

    PLANNER = "planner"


#: Every **live** Phase-2 semantic cross-check finding is a manifest-declaration issue the Planner
#: owns, so it routes to the Planner (``§6.5``/``§6.10``). The reserved Phase-3 codes have no Phase-2
#: route (they are inert) and are absent here.
_LIVE_FINDING_ROUTES: dict[SemanticCrossCheckCode, ResponsibleAgent] = {
    SemanticCrossCheckCode.MISSING_IMPLIED_FACET: ResponsibleAgent.PLANNER,
    SemanticCrossCheckCode.INCOMPATIBLE_FACETS: ResponsibleAgent.PLANNER,
    SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE: ResponsibleAgent.PLANNER,
}


def responsible_agent_for(finding: SemanticCrossCheckFinding) -> ResponsibleAgent:
    """The agent the coordinator re-runs for ``finding`` (``validation.md §6.5``/``§6.10``).

    A pure mapping the orchestrator/coordinator consumes — the validator itself never routes
    (``architecture.md §1.5``). Raises :class:`NotImplementedError` for a reserved Phase-3 code: the
    code-vs-manifest checks are inert this phase, so no live finding carries one.
    """
    try:
        return _LIVE_FINDING_ROUTES[finding.code]
    except KeyError:
        raise NotImplementedError(
            f"no Phase-2 route for {finding.code.value!r}: the code-vs-manifest checks "
            "(routed to the Phase-3 Generators) are inert this phase"
        ) from None


# --- inert (Phase-3) code-vs-manifest check --------------------------------


def references_lab_outputs_findings(manifest: LabManifest) -> list[SemanticCrossCheckFinding]:
    """The ``references_lab_outputs`` bidirectional cross-check — **inert until Phase 3**.

    ``validation.md §6.5`` specifies two directions, both comparing the manifest against *generated
    IaC* (which does not exist before the Phase-3 Generators run):

    - a per-phase IaC ``references_lab_outputs`` entry that is absent from the lab-level IaC's
      outputs → ``UNDECLARED_LAB_OUTPUT_REFERENCE`` (a Lab-level Generator failure);
    - a per-phase IaC reference to a ``lab_resources`` entry the Planner never declared →
      ``UNDECLARED_LAB_RESOURCE_REFERENCE`` (a per-phase Generator failure).

    Both need the generated reference set, so this returns nothing in Phase 2. Phase 3 extends the
    signature with the generated-IaC references and fills the body — the codes and the routing
    already exist, so Phase 3 does not re-derive them (ADR 0094).
    """
    _ = manifest  # the manifest half alone is insufficient; the check needs generated IaC (Phase 3).
    return []


# --- the validator ---------------------------------------------------------


class SemanticCrossCheckValidator:
    """Runs the semantic cross-check over a ``LabManifest`` (``validation.md §6.5``).

    Constructed with the merged registries (bundled + overlay) — needed for facet ``implies`` /
    ``incompatible_with`` resolution. Stateless across calls; never mutates its inputs.
    """

    def __init__(self, *, registries: MergedRegistries) -> None:
        self._registries = registries

    # --- public surface ----------------------------------------------------

    def validate(self, manifest: LabManifest) -> SemanticCrossCheckResult:
        """Validate ``manifest`` and return a ``SemanticCrossCheckResult``.

        Runs the live cross-block-within-manifest checks (facet ``implies`` / ``incompatible_with``,
        ``produces_world_state`` ``identifier_source`` resolution). Read-only: never raises on an
        inconsistency — those are findings; the orchestrator decides routing
        (``architecture.md §1.5``). The code-vs-manifest checks are inert this phase
        (:func:`references_lab_outputs_findings`); there is no ``affected_platforms`` check — it is
        moot by design (platforms are facet-derived, ``schema.md §4.4``; ADR 0095).
        """
        findings: list[SemanticCrossCheckFinding] = []
        findings.extend(self._check_facet_implies(manifest))
        findings.extend(self._check_facet_incompatibilities(manifest))
        findings.extend(self._check_identifier_sources(manifest))

        passed = not findings
        if not passed:
            logger.info("semantic cross-check failed with %d finding(s)", len(findings))
        return SemanticCrossCheckResult(passed=passed, findings=findings)

    # --- check 1: facet implies -------------------------------------------

    def _check_facet_implies(self, manifest: LabManifest) -> list[SemanticCrossCheckFinding]:
        """Every facet a declared facet ``implies`` must itself be declared (``schema.md §4.13``)."""
        findings: list[SemanticCrossCheckFinding] = []
        declared = set(manifest.facets)
        for i, facet in enumerate(manifest.facets):
            entry = self._registries.facet(facet)
            if entry is None:
                # An unknown facet is a static-schema concern, not this pass's.
                continue
            for implied in entry.implies:
                if implied not in declared:
                    findings.append(
                        SemanticCrossCheckFinding(
                            code=SemanticCrossCheckCode.MISSING_IMPLIED_FACET,
                            location=f"facets[{i}]",
                            detail=(
                                f"facet {facet!r} implies {implied!r}, which is not declared; the "
                                "Planner must add it (the Validator does not author manifest content)"
                            ),
                        )
                    )
        return findings

    # --- check 2: facet incompatible_with ---------------------------------

    def _check_facet_incompatibilities(
        self, manifest: LabManifest
    ) -> list[SemanticCrossCheckFinding]:
        """Declared facets that are ``incompatible_with`` each other are a finding (symmetric).

        Each contradictory unordered pair is reported once, regardless of which side declares the
        incompatibility or the declaration order in the manifest.
        """
        findings: list[SemanticCrossCheckFinding] = []
        declared = set(manifest.facets)
        seen_pairs: set[tuple[str, str]] = set()
        for i, facet in enumerate(manifest.facets):
            entry = self._registries.facet(facet)
            if entry is None:
                continue
            for other in entry.incompatible_with:
                if other not in declared:
                    continue
                pair = (facet, other) if facet < other else (other, facet)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                findings.append(
                    SemanticCrossCheckFinding(
                        code=SemanticCrossCheckCode.INCOMPATIBLE_FACETS,
                        location=f"facets[{i}]",
                        detail=(
                            f"facets {facet!r} and {other!r} are declared incompatible_with each "
                            "other; at most one may be declared"
                        ),
                    )
                )
        return findings

    # --- check 3: produces_world_state identifier_source resolution -------

    def _check_identifier_sources(self, manifest: LabManifest) -> list[SemanticCrossCheckFinding]:
        """Each ``runtime_generated`` ``identifier_source`` resolves to a declared phase output.

        The documented form is ``phase_outputs.<name>`` where ``<name>`` is a key in the phase's
        ``outputs`` block (``schema.md §4.5``, ``§6.5``). A value lacking the prefix or naming an
        undeclared output is flagged — without this, cleanup code reads a source that never exists.
        """
        findings: list[SemanticCrossCheckFinding] = []
        for p_i, phase in enumerate(manifest.phases):
            output_names = {output.name for output in phase.outputs}
            for w_i, pws in enumerate(phase.produces_world_state):
                if pws.identifier_kind is not IdentifierKind.RUNTIME_GENERATED:
                    continue
                source = pws.identifier_source
                resolved = (
                    source is not None
                    and source.startswith(_PHASE_OUTPUTS_PREFIX)
                    and source.removeprefix(_PHASE_OUTPUTS_PREFIX) in output_names
                )
                if not resolved:
                    findings.append(
                        SemanticCrossCheckFinding(
                            code=SemanticCrossCheckCode.UNRESOLVED_IDENTIFIER_SOURCE,
                            location=f"phases[{p_i}].produces_world_state[{w_i}].identifier_source",
                            detail=(
                                f"identifier_source {source!r} does not resolve to a declared output "
                                f"of phase {phase.id!r}; expected 'phase_outputs.<name>' where <name> "
                                f"is one of {sorted(output_names)}"
                            ),
                        )
                    )
        return findings


__all__ = [
    "ResponsibleAgent",
    "SemanticCrossCheckCode",
    "SemanticCrossCheckFinding",
    "SemanticCrossCheckResult",
    "SemanticCrossCheckValidator",
    "references_lab_outputs_findings",
    "responsible_agent_for",
]
