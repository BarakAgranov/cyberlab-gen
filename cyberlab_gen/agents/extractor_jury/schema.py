"""The JuryVerdict schema emitted by the Extractor-Jury (``agents.md §5.5``).

ADR 0021. The jury's typed output is a ``JuryVerdict``:
``{verdict, scores, feedback, retry_recommended}``. The framework — not the jury
— maps the verdict to control flow (``architecture.md §1.5``):

- ``approve`` → continue;
- ``revise`` (1-3 fields with citation problems) -> field-targeted Extractor
  re-run (counts against the refinement budget);
- ``reject`` (>30 % of content fields mismatched: systematic hallucination) ->
  pipeline halts.

The rubric has four dimensions (fidelity, completeness, provenance correctness,
structural validity), each scored 0-1, with a default floor of 0.7 (a v1
placeholder pending eval data, ``architecture.md §8.4``). ``ArtifactModel``
because the verdict is surfaced in the run report and may round-trip through
YAML.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.primitives import NonEmptyString

#: Maximum field-level feedback items consistent with a ``revise`` verdict
#: (``agents.md §5.5``: "1-3 content fields with mismatched citations").
MAX_REVISE_FEEDBACK = 3


class Verdict(StrEnum):
    """The three jury verdicts (``agents.md §5.5``)."""

    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class JuryScores(ArtifactModel):
    """The four rubric dimensions, each 0-1 (``agents.md §5.5``)."""

    fidelity: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    provenance_correctness: float = Field(ge=0.0, le=1.0)
    structural_validity: float = Field(ge=0.0, le=1.0)

    def min_dimension(self) -> float:
        """Lowest of the four dimension scores (the value compared to the floor)."""
        return min(
            self.fidelity,
            self.completeness,
            self.provenance_correctness,
            self.structural_validity,
        )

    def all_above(self, floor: float) -> bool:
        """True when every dimension is at or above ``floor``."""
        return self.min_dimension() >= floor


class JuryFieldFeedback(ArtifactModel):
    """A single field-targeted concern the jury raised.

    ``field_path`` uses the same JSONPath-like convention as ``GapEntry`` so the
    Extractor's re-run can target the named field.
    """

    field_path: NonEmptyString
    problem: NonEmptyString
    suggested_fix: str | None = None


class JuryVerdict(ArtifactModel):
    """The Extractor-Jury's structured judgment (``agents.md §5.5``, ADR 0021).

    The ``_verdict_consistency`` validator enforces the verdict↔feedback↔score
    coupling so a malformed verdict fails *structurally* rather than silently
    mis-routing control flow:

    - ``approve`` → no feedback items;
    - ``revise`` → 1-``MAX_REVISE_FEEDBACK`` feedback items;
    - ``reject`` → at least one feedback item (the systematic-hallucination case).
    """

    verdict: Verdict
    scores: JuryScores
    feedback: list[JuryFieldFeedback] = Field(default_factory=list[JuryFieldFeedback])
    retry_recommended: bool
    rationale: NonEmptyString

    @model_validator(mode="after")
    def _verdict_consistency(self) -> Self:
        n = len(self.feedback)
        if self.verdict is Verdict.APPROVE and n != 0:
            raise ValueError("approve verdict must carry no field feedback")
        if self.verdict is Verdict.REVISE and not (1 <= n <= MAX_REVISE_FEEDBACK):
            raise ValueError(
                f"revise verdict must carry 1-{MAX_REVISE_FEEDBACK} field feedback items (got {n})"
            )
        if self.verdict is Verdict.REJECT and n < 1:
            raise ValueError("reject verdict must name at least one mismatched field")
        return self


__all__ = [
    "MAX_REVISE_FEEDBACK",
    "JuryFieldFeedback",
    "JuryScores",
    "JuryVerdict",
    "Verdict",
]
