"""Provenance wrapper and citation block for content fields.

Architectural source: ``schema-details.md`` §3; ``schema.md`` §4.9.

Every content field on an artifact (AttackSpec, LabManifest) wraps its value
in ``Provenance[T]`` so the source (blog, API, LLM inference, unknown, user),
citations, optional confidence, and the pre-Planner enrichment discrepancy
record travel with the value. Structural fields (ids, paths, type
references) do *not* wrap in Provenance.

``_source_rules`` enforces the cross-field invariants spelled out in §3:
required-when (LLM_INFERENCE -> confidence; UNKNOWN_FROM_BLOG -> reason;
BLOG_EXPLICIT/EXTERNAL_API -> citations), the confidence/confidence_source
pairing, and the discrepancy-with-blog audit-trail rules.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ConfidenceSource,
    ProvenanceSource,
)
from cyberlab_gen.schemas.primitives import NonEmptyString


class CitationBlock(ArtifactModel):
    """A single citation supporting a provenance claim.

    For ``BLOG_PASSAGE``: ``reference`` is a passage identifier such as
    "§3, ¶2" or character offsets. For ``EXTERNAL_API_RESPONSE``: API name
    plus endpoint plus response path. For ``LLM_REASONING_TRACE``: a stable
    trace identifier. For ``USER_INPUT``: a run-id plus interrupt-step
    identifier. schema.md §4.9.
    """

    kind: CitationKind
    reference: NonEmptyString
    location: str | None = None


class Provenance[T](ArtifactModel):
    """Wraps a content-field value with its source, citations, and confidence.

    Generic in T so a content field's value type is preserved: a string
    field becomes ``Provenance[str]``, a list-of-strings field becomes
    ``Provenance[list[str]]``, and so on. The convenience aliases at the
    bottom of this module cover the common Ts.

    ``discrepancy_with_blog`` and its companion fields capture pre-Planner
    enrichment overrides per ``pipeline.md`` §3.2.4 -- when an external API
    contradicts the blog and the framework picks the API, the original
    blog value is preserved here for the audit trail. schema.md §4.9.
    """

    value: T
    source: ProvenanceSource
    citations: list[CitationBlock] = Field(default_factory=list[CitationBlock])
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_source: ConfidenceSource | None = None
    requires_user_confirmation: bool = False
    reason: str | None = None
    discrepancy_with_blog: bool = False
    overridden_blog_value: T | None = None
    discrepancy_classification: Literal["material", "non_material"] | None = None

    @model_validator(mode="after")
    def _source_rules(self) -> Self:
        # Required-when invariants.
        if self.source is ProvenanceSource.LLM_INFERENCE and self.confidence is None:
            raise ValueError("confidence is required when source is llm_inference")
        if self.source is ProvenanceSource.UNKNOWN_FROM_BLOG and not self.reason:
            raise ValueError("reason is required when source is unknown_from_blog")
        if self.source is ProvenanceSource.BLOG_EXPLICIT and not self.citations:
            raise ValueError("citations are required when source is blog_explicit")
        if self.source is ProvenanceSource.EXTERNAL_API and not self.citations:
            raise ValueError("citations are required when source is external_api")

        # confidence and confidence_source travel together.
        if self.confidence is not None and self.confidence_source is None:
            raise ValueError("confidence_source is required when confidence is set")
        if self.confidence is None and self.confidence_source is not None:
            raise ValueError("confidence_source must be None when confidence is None")
        # Confidence is exclusive to LLM_INFERENCE.
        # See dev/decisions/0005-external-api-confidence.md.
        if self.confidence is not None and self.source is not ProvenanceSource.LLM_INFERENCE:
            raise ValueError(
                f"confidence is only valid when source is llm_inference "
                f"(got source={self.source.value})"
            )
        if self.confidence_source is not None and self.source is not ProvenanceSource.LLM_INFERENCE:
            raise ValueError(
                f"confidence_source is only valid when source is llm_inference "
                f"(got source={self.source.value})"
            )

        # Negative invariants — UNKNOWN_FROM_BLOG citations check.
        # The confidence-must-be-None case is covered by the LLM_INFERENCE-exclusive rule above.
        if self.source is ProvenanceSource.UNKNOWN_FROM_BLOG and self.citations:
            raise ValueError("citations must be empty when source is unknown_from_blog")

        # Discrepancy-record invariants.
        if self.discrepancy_with_blog:
            if self.overridden_blog_value is None:
                raise ValueError(
                    "overridden_blog_value is required when discrepancy_with_blog is true"
                )
            if self.discrepancy_classification is None:
                raise ValueError(
                    "discrepancy_classification is required when discrepancy_with_blog is true"
                )
            if self.source is not ProvenanceSource.EXTERNAL_API:
                raise ValueError(
                    "discrepancy_with_blog is only valid when source is external_api "
                    "(the framework overrode a blog value with an authoritative API value)"
                )
        else:
            if self.overridden_blog_value is not None:
                raise ValueError(
                    "overridden_blog_value must be None when discrepancy_with_blog is false"
                )
            if self.discrepancy_classification is not None:
                raise ValueError(
                    "discrepancy_classification must be None when discrepancy_with_blog is false"
                )
        return self


# Convenience aliases for the common content-field value types.
ProvenanceString = Provenance[str]
ProvenanceStringList = Provenance[list[str]]
ProvenanceFloat = Provenance[float]
ProvenanceInt = Provenance[int]
ProvenanceBool = Provenance[bool]

__all__ = [
    "CitationBlock",
    "Provenance",
    "ProvenanceBool",
    "ProvenanceFloat",
    "ProvenanceInt",
    "ProvenanceString",
    "ProvenanceStringList",
]
