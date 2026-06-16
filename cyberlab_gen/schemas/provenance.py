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

from collections.abc import Callable
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ConfidenceSource,
    ProvenanceSource,
)
from cyberlab_gen.schemas.framework_owned import FrameworkOwned
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
    # The API-override discrepancy record + framework_enriched are framework-owned (ADR 0087):
    # the enrichment pass is their sole author. The inline marker is the one declaration the
    # patch-path check and the whole-spec reset both derive from.
    discrepancy_with_blog: Annotated[bool, FrameworkOwned()] = False
    overridden_blog_value: Annotated[T | None, FrameworkOwned()] = None
    discrepancy_classification: Annotated[
        Literal["material", "non_material"] | None, FrameworkOwned()
    ] = None
    # Set by the pre-Planner enrichment pass (``pipeline.md §3.2.4``) on every field it
    # writes/rewrites: ``external_api`` + ``framework_enriched=True`` is the framework's own
    # authoritative call (the API-response citation IS the evidence — no agent tool-call
    # required), distinct from an agent-claimed ``external_api`` value (which must have matching
    # trace evidence, search-before-claim). The only *mechanical* exemption is the grounding
    # stack's CVE-scoped search-before-claim check (``grounding_validator.py``); the Extractor-Jury
    # is an LLM agent that consumes that findings set and has no independent mechanical exemption of
    # its own (ADR 0052 / 0061, schema.md §4.9).
    framework_enriched: Annotated[bool, FrameworkOwned()] = False

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

        # framework_enriched marks the framework's own authoritative external_api call (ADR
        # 0052 / 0061); it is only meaningful on source=external_api. enrichment is the only
        # writer (``schema.md §4.9`` framework-only authorship).
        if self.framework_enriched and self.source is not ProvenanceSource.EXTERNAL_API:
            raise ValueError(
                "framework_enriched is only valid when source is external_api "
                f"(got source={self.source.value})"
            )

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

    def __reduce__(
        self,
    ) -> tuple[Callable[..., "Provenance[Any]"], tuple[tuple[Any, ...], dict[str, Any]]]:
        # Make every Provenance[T] picklable — including custom-enum args like
        # Provenance[Severity] (CveReference.severity / DetectionBlock.severity). ADR 0066.
        #
        # The checkpointer falls the whole HttpUrl-bearing AttackSpec subtree to
        # ``pickle_fallback`` (ADR 0040), so every nested Provenance must pickle. Pydantic's
        # default reduce pickles a generic instance by *reference* to its parametrized class
        # (``Provenance[Severity]``), which only resolves when pydantic registered that
        # parametrization in the module namespace. It does that ONLY for parametrizations
        # first created at module-global scope (``create_generic_submodel`` →
        # ``_get_caller_frame_info``): the builtin aliases below qualify, but
        # ``Provenance[Severity]`` is first built lazily inside pydantic's schema construction
        # for the models that reference it (a non-global frame) and cached, so it is never
        # registered and the by-reference pickle raises ``PicklingError``. Reconstruct via the
        # generic origin + args instead, so the whole family round-trips deterministically,
        # independent of import order. (The msgpack/registered path of ADR 0066 cannot help
        # here: the HttpUrl forces the entire spec to pickle, so Provenance never reaches it.)
        type_args = self.__pydantic_generic_metadata__["args"]
        return (_rebuild_provenance, (type_args, self.__getstate__()))


def _rebuild_provenance(type_args: tuple[Any, ...], state: dict[str, Any]) -> Provenance[Any]:
    """Reconstruct a (possibly parametrized) ``Provenance`` while unpickling (ADR 0066).

    ``type_args`` is the pydantic generic-metadata ``args`` tuple (e.g. ``(Severity,)``);
    re-subscripting ``Provenance`` rebuilds the exact concrete class so the round-tripped
    instance preserves ``type(obj)`` and compares equal. This function lives at module scope
    — and so is itself picklable by reference — precisely because the parametrized classes
    are not. State is restored via pydantic's own ``__setstate__`` (the same payload its
    default reduce uses), so no field re-validation runs.
    """
    cls: type[Provenance[Any]] = Provenance[type_args[0]] if type_args else Provenance
    obj = cls.__new__(cls)
    obj.__setstate__(state)
    return obj


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
