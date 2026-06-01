"""Manual jury-decision review tooling: false-approval / false-rejection rates.

Architectural source: ``eval.md §7.5`` (false-approval / false-rejection rates,
asymmetric calibration), ``implementation-plan.md §4.2`` "Manual jury-decision
review tooling: maintainer reads the AttackSpec, marks each jury verdict as
correct/false-approval/false-rejection", §4.4 (the rates calibrate the jury
threshold). Shape pinned in ADR 0025.

The tool *measures*; it does not judge. A maintainer reads each AttackSpec
against its ground-truth walk and supplies a :class:`ReviewMark` per run. The tool
aggregates per-blog and overall false-approval / false-rejection rates. The
**asymmetric discipline** (``CALIBRATION.md``) governs how the *rates* feed
threshold tuning — this module never auto-adjusts a floor; it only computes the
two rates so a maintainer can tighten on false-approval (never loosen on
false-rejection).

Definitions (``eval.md §7.5``):

- **false-approval**: the jury approved an AttackSpec the human reference marks as
  needing revision. Costlier — bad foundations cascade.
- **false-rejection**: the jury demanded revisions on an AttackSpec the human
  reference marks as acceptable. Costs cycles, doesn't corrupt outputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from cyberlab_gen.schemas.base import ArtifactModel


class ReviewMark(StrEnum):
    """A maintainer's judgment of one jury verdict against the ground-truth walk."""

    CORRECT = "correct"
    FALSE_APPROVAL = "false_approval"
    FALSE_REJECTION = "false_rejection"


class JuryReviewEntry(ArtifactModel):
    """One reviewed jury decision (``eval.md §7.5``).

    ``blog_id`` + ``run_index`` locate the run; ``mark`` is the maintainer's
    judgment; ``note`` optionally records why (e.g., "jury approved but step 3's
    citation points at the wrong passage").
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    run_index: int = Field(ge=0)
    mark: ReviewMark
    note: str | None = None


class BlogReviewRates(ArtifactModel):
    """Per-blog false-approval / false-rejection rates (``eval.md §7.5``)."""

    model_config = ConfigDict(frozen=True)

    blog_id: str
    reviewed: int = Field(ge=0)
    false_approvals: int = Field(ge=0)
    false_rejections: int = Field(ge=0)

    @property
    def false_approval_rate(self) -> float:
        """Share of reviewed verdicts that were false approvals (0 when none reviewed)."""
        return self.false_approvals / self.reviewed if self.reviewed else 0.0

    @property
    def false_rejection_rate(self) -> float:
        """Share of reviewed verdicts that were false rejections (0 when none reviewed)."""
        return self.false_rejections / self.reviewed if self.reviewed else 0.0


class JuryReviewLedger(ArtifactModel):
    """The maintainer's reviewed jury decisions + the aggregated rates (ADR 0025).

    ``ArtifactModel`` because the ledger is archived alongside eval reports
    (``eval.md §7.13``) and round-trips through YAML. Entries are added via
    :meth:`with_mark` (frozen-friendly: returns a new ledger) so the model stays
    immutable.
    """

    model_config = ConfigDict(frozen=True)

    spec_version: int = 1
    spec_kind: str = "JuryReviewLedger"
    rotation_generation: int = Field(ge=0)
    entries: list[JuryReviewEntry] = Field(default_factory=list[JuryReviewEntry])

    def with_mark(
        self, *, blog_id: str, run_index: int, mark: ReviewMark, note: str | None = None
    ) -> JuryReviewLedger:
        """Return a new ledger with one more reviewed decision appended."""
        entry = JuryReviewEntry(blog_id=blog_id, run_index=run_index, mark=mark, note=note)
        return self.model_copy(update={"entries": [*self.entries, entry]})

    def per_blog_rates(self) -> list[BlogReviewRates]:
        """Aggregate false-approval / false-rejection rates per blog (``eval.md §7.5``)."""
        by_blog: dict[str, list[JuryReviewEntry]] = {}
        for e in self.entries:
            by_blog.setdefault(e.blog_id, []).append(e)
        out: list[BlogReviewRates] = []
        for blog_id, items in by_blog.items():
            out.append(
                BlogReviewRates(
                    blog_id=blog_id,
                    reviewed=len(items),
                    false_approvals=sum(1 for i in items if i.mark is ReviewMark.FALSE_APPROVAL),
                    false_rejections=sum(1 for i in items if i.mark is ReviewMark.FALSE_REJECTION),
                )
            )
        return out

    def overall_false_approval_rate(self) -> float:
        """False-approval rate across every reviewed verdict (``eval.md §7.5``)."""
        if not self.entries:
            return 0.0
        return sum(1 for e in self.entries if e.mark is ReviewMark.FALSE_APPROVAL) / len(
            self.entries
        )

    def overall_false_rejection_rate(self) -> float:
        """False-rejection rate across every reviewed verdict (``eval.md §7.5``)."""
        if not self.entries:
            return 0.0
        return sum(1 for e in self.entries if e.mark is ReviewMark.FALSE_REJECTION) / len(
            self.entries
        )


__all__ = [
    "BlogReviewRates",
    "JuryReviewEntry",
    "JuryReviewLedger",
    "ReviewMark",
]
