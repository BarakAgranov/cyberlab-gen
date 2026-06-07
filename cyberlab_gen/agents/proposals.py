"""In-flight registry proposals emitted by agents via the propose_* tools.

Architectural source: ``schema.md §4.16`` (proposal lifecycle), ADR 0021.

These are the proposal objects an *agent* authors at runtime through the
``propose_value_type`` / ``propose_facet`` tools — the *content* of a would-be
registry entry plus the agent's reasoning. They are deliberately **not** the
overlay-resident ``ProposalAuditBlock`` (``schemas/registries.py``): that block
holds framework-recorded acceptance metadata (``proposed_by_model``,
``proposed_at``) the agent must not author (``schema.md §4.16`` — "framework-
recorded, not agent-authored"). The framework stamps the audit metadata and
writes the overlay entry at accept time (Task 7's interrupt / ``--auto``).

``InternalModel`` (``extra="ignore"``): a proposal is a stage-internal object
carried inside ``ExtractionResult``, never written to disk as an artifact (only
the accepted overlay *entry* is). The proposal authority split — the Extractor
proposes ``value_types`` and ``target:*`` / blog-derived ``lab_class_signal:*``
facets only — is enforced at the tool boundary (``extractor.tools``), not here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.registries import FacetEntry, ValueTypeEntry

#: Facet categories the Extractor is allowed to propose (``schema.md §4.16``).
#: ``runtime`` and lab-derived ``lab_class_signal`` belong to the Planner.
EXTRACTOR_FACET_CATEGORIES: frozenset[str] = frozenset({"target", "lab_class_signal"})


class ProposedValueType(InternalModel):
    """An Extractor-proposed ``value_types`` registry entry (in flight).

    Mirrors the *content* fields of ``ValueTypeEntry`` (``schemas/registries.py``)
    that an agent can author. ``proposed_by`` / ``proposed_in_run`` and the audit
    block are framework-stamped at accept time, not here.
    """

    name: str
    description: str
    # JSON Schema for the value's shape (open-shape, same argument as ValueTypeEntry).
    value_schema: dict[str, Any] = Field(default_factory=dict[str, Any])
    sensitive: bool = False
    notes_for_generator: str | None = None
    platforms: list[str] = Field(default_factory=list[str])
    # The agent's justification — becomes the audit block's ``reasoning`` at accept.
    reasoning: str

    def to_entry(self, *, proposed_in_run: str | None = None) -> ValueTypeEntry:
        """Convert this in-flight proposal to a ``value_types`` overlay entry (ADR 0044).

        The framework stamps ``proposed_by='extractor'`` (the only value-type
        proposer) and the run id; the agent never authors those.
        """
        return ValueTypeEntry(
            name=self.name,
            description=self.description,
            schema=self.value_schema,  # ValueTypeEntry aliases ``schema_`` to ``schema``
            sensitive=self.sensitive,
            notes_for_generator=self.notes_for_generator,
            platforms=self.platforms,
            proposed_by="extractor",
            proposed_in_run=proposed_in_run,
        )


class ProposedFacet(InternalModel):
    """An Extractor-proposed ``facets`` registry entry (in flight).

    ``category`` is constrained to the Extractor's authority
    (``target`` | ``lab_class_signal``); a ``runtime`` proposal is rejected at the
    tool boundary before a ``ProposedFacet`` is ever constructed (ADR 0021).
    """

    name: str
    category: Literal["target", "lab_class_signal"]
    description: str
    applies_at_levels: list[Literal["lab", "phase", "step"]] = Field(min_length=1)
    reasoning: str

    def to_entry(self) -> FacetEntry:
        """Convert this in-flight proposal to a ``facets`` overlay entry (ADR 0044).

        The framework stamps ``proposed_by='extractor'``; the agent never authors it.
        The category was already gated to the Extractor's authority at the tool
        boundary (``extractor.tools``), so it is carried through unchanged.
        """
        return FacetEntry(
            name=self.name,
            category=self.category,
            proposed_by="extractor",
            description=self.description,
            applies_at_levels=self.applies_at_levels,
        )


__all__ = [
    "EXTRACTOR_FACET_CATEGORIES",
    "ProposedFacet",
    "ProposedValueType",
]
