"""Cross-boundary agent result contracts, in a leaf module (ADR 0075).

``ExtractionResult`` is *produced* by the Extractor (``agents``) and *consumed* by the orchestrator
(``framework``). Defining it here — a module that imports neither ``framework`` nor the orchestrator
— lets both sides import it at top level, dissolving the ``agents``↔``framework`` load-time cycle
that previously forced the orchestrator's runtime import of ``agents.extractor.extractor`` and the
Extractor's lazy import of ``framework.refinement``.
"""

from __future__ import annotations

# Runtime imports (not TYPE_CHECKING): these are the field types of a Pydantic model, so Pydantic
# must resolve them at class-definition time. None of them import ``framework`` at runtime, so this
# module stays a leaf.
from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
from cyberlab_gen.agents.proposals import ProposedFacet, ProposedThesisType, ProposedValueType
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.manifest import LabManifest


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


class PlanResult(InternalModel):
    """The Planner stage's output envelope (ADR 0090).

    Wraps the finalized ``LabManifest`` (the only piece that becomes an artifact — its lab-level
    ``core.reproducibility`` already framework-derived by ``Planner.plan``) plus the external-lookup
    trace the framework may consume downstream. For the Wave-1 slice the Planner is non-proposing
    (``propose_facet`` is Task 7), so there is no proposal side-channel yet; Task 7 adds
    ``facet_proposals`` here.
    """

    manifest: LabManifest
    lookups: list[ExternalLookupRecord]


__all__ = ["ExtractionResult", "PlanResult"]
