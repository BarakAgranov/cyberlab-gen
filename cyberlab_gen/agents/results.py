"""Cross-boundary agent result contracts, in a leaf module (ADR 0075).

``ExtractionResult`` is *produced* by the Extractor (``agents``) and *consumed* by the orchestrator
(``framework``); ``PlanAttempt`` / ``PlanResult`` are the Planner's equivalent. Defining them here —
a module that imports neither ``framework`` nor the orchestrator, and crucially **not** the
``agents.planner`` / ``agents.extractor`` *packages* (only their leaf ``tools`` submodules) — lets
both sides import at top level, dissolving the ``agents``↔``framework`` load-time cycle that
previously forced the orchestrator's runtime import of ``agents.extractor.extractor`` and the
Extractor's lazy import of ``framework.refinement``.

The Planner's discriminated output (``PlanAttempt`` + ``PlanOutcome`` + ``PlannerRefusal``, ADR 0092)
lives here for the same reason: ``PlanResult`` references them, and routing them through the
``agents.planner`` package surface would re-introduce the cycle (importing a submodule runs the
package ``__init__``, which imports ``planner.py`` → ``extractor.extractor`` mid-init). The
``agents.planner`` surface re-exports them from here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

# Runtime imports (not TYPE_CHECKING): these are the field types of a Pydantic model, so Pydantic
# must resolve them at class-definition time. None of them import ``framework`` at runtime, and the
# two ``agents`` imports are leaf submodules (``extractor.tools`` / ``proposals``) that do not import
# this module back, so this module stays a leaf.
from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
from cyberlab_gen.agents.proposals import ProposedFacet, ProposedThesisType, ProposedValueType
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.schemas.primitives import NonEmptyString


class ExtractionResult(InternalModel):
    """The Extractor stage's output envelope (ADR 0021).

    Wraps the validated ``AttackSpec`` (the only piece that becomes an artifact) plus the
    side-channel the framework needs downstream: the registry proposals the agent emitted, the
    external-lookup trace (which the orchestrator-owned grounding stack consumes for
    search-before-claim), and how many content-level re-prompts the targeted patch took (0 for a
    clean first extract).
    """

    attack_spec: AttackSpec
    value_type_proposals: list[ProposedValueType]
    facet_proposals: list[ProposedFacet]
    thesis_type_proposals: list[ProposedThesisType]
    lookups: list[ExternalLookupRecord]
    reprompts: int = 0


class PlanOutcome(StrEnum):
    """The three Planner outcomes (``agents.md §5.7``). The framework routes on these (``§1.5``)."""

    PLANNED = "planned"
    #: The AttackSpec is incoherent in a way the Extractor missed (mismatched pre/postconditions, a
    #: value type the AttackSpec never typed) → route back to the Extractor. The Planner does NOT
    #: repair it (``agents.md §5.7``).
    ATTACKSPEC_INCOHERENT = "attackspec_incoherent"
    #: AttackSpec gaps too large to plan around, or infrastructure the system cannot express →
    #: the Planner refuses outright; the run halts with the gap report (``pipeline.md §3.2.6``).
    CANNOT_PLAN = "cannot_plan"


class PlannerRefusal(ArtifactModel):
    """Structured detail accompanying a non-``planned`` outcome (``pipeline.md §3.2.6``).

    Carries *which* AttackSpec content prevented planning so the framework can route precisely and
    the run report / route-back feedback is actionable — never a bare "cannot plan" string. Used for
    both ``attackspec_incoherent`` (the route-back detail) and ``cannot_plan`` (the gap report).
    """

    summary: NonEmptyString
    #: The specific AttackSpec field paths implicated (≥1) — dotted + integer-index convention, the
    #: same locator shape the jury / refinement patch paths use.
    attack_spec_field_paths: list[NonEmptyString] = Field(min_length=1)
    detail: NonEmptyString


class PlanAttempt(ArtifactModel):
    """The Planner's forced output: a planned manifest XOR a structured refusal (ADR 0092).

    The Planner forces this — not a bare ``LabManifest`` — because an incoherent or un-plannable
    AttackSpec has **no valid manifest to emit**: the Planner needs an in-band channel to surface "I
    cannot produce a manifest, and here is the structured why" (``architecture.md §1.5``: the LLM
    produces a structured judgment; the framework routes on it — the Planner never repairs the
    AttackSpec). This mirrors the *spirit* of the Extractor's in-band ``extraction_outcome``
    discriminator (an outcome enum + a coupling validator) but as a **wrapper**, because — unlike an
    out-of-scope AttackSpec, which is still a complete spec with its content blocks nulled — a failed
    plan carries no manifest at all.

    The ``_outcome_consistency`` validator enforces the discriminator↔payload coupling so a malformed
    attempt fails *structurally* rather than silently mis-routing control flow (mirrors
    ``JuryVerdict``'s verdict↔feedback validator):

    - ``planned`` → ``manifest`` set, ``refusal`` ``None``;
    - ``attackspec_incoherent`` / ``cannot_plan`` → ``refusal`` set, ``manifest`` ``None``.

    The framework reads ``outcome`` to route: ``planned`` → Planner-Jury; ``attackspec_incoherent``
    → route back to the Extractor (refinement); ``cannot_plan`` → halt with the gap report.
    """

    outcome: PlanOutcome
    manifest: LabManifest | None = None
    refusal: PlannerRefusal | None = None

    @model_validator(mode="after")
    def _outcome_consistency(self) -> Self:
        if self.outcome is PlanOutcome.PLANNED:
            if self.manifest is None:
                raise ValueError("planned outcome must carry a manifest")
            if self.refusal is not None:
                raise ValueError("planned outcome must not carry a refusal")
        else:
            if self.manifest is not None:
                raise ValueError(f"{self.outcome.value} outcome must not carry a manifest")
            if self.refusal is None:
                raise ValueError(f"{self.outcome.value} outcome must carry a refusal")
        return self


class PlanResult(InternalModel):
    """The Planner stage's output envelope (ADR 0090, extended by ADR 0092).

    Carries the Planner's ``outcome`` (the framework routes on it, ``architecture.md §1.5``) plus the
    matching payload: on ``planned`` the finalized ``LabManifest`` (the only piece that becomes an
    artifact — its lab-level ``core.reproducibility`` already framework-derived by ``Planner.plan`` /
    ``Planner.refine``); on ``cannot_plan`` / ``attackspec_incoherent`` a structured
    :class:`PlannerRefusal` and ``manifest=None`` (the manifest is optional because a failed plan has
    none — the ADR-0090 contract evolution, small and with no consumer yet). ``lookups`` is the
    external-lookup trace; ``reprompts`` counts targeted-patch re-prompts on the ``refine`` path (0
    for a clean ``plan``). For the Wave-1 slice the Planner is non-proposing (``propose_facet`` is
    Task 7), so there is no proposal side-channel yet; Task 7 adds ``facet_proposals`` here.
    """

    outcome: PlanOutcome
    manifest: LabManifest | None = None
    refusal: PlannerRefusal | None = None
    lookups: list[ExternalLookupRecord]
    reprompts: int = 0


__all__ = [
    "ExtractionResult",
    "PlanAttempt",
    "PlanOutcome",
    "PlanResult",
    "PlannerRefusal",
]
