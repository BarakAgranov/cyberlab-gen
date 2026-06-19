"""Manual jury-decision review tooling: false-approval / false-rejection rates.

Architectural source: ``eval.md §7.5`` (false-approval / false-rejection rates,
asymmetric calibration), ``implementation-plan.md §4.2`` "Manual jury-decision
review tooling: maintainer reads the artifact, marks each jury verdict as
correct/false-approval/false-rejection", §4.4 / §5.4 (the rates calibrate the jury
thresholds). Shape pinned in ADR 0025; the **per-jury split** (Extractor-Jury vs
Planner-Jury) added in **ADR 0102** (Phase-2 Task 10).

The tool *measures*; it does not judge. A maintainer reads each artifact (an AttackSpec for the
Extractor-Jury, a LabManifest for the Planner-Jury) against its ground-truth walk and supplies a
:class:`ReviewMark` per run, tagged with which :class:`JuryKind` the verdict came from. The tool
aggregates per-blog, per-jury, and overall false-approval / false-rejection rates. The **asymmetric
discipline** (``CALIBRATION.md``) governs how the *rates* feed threshold tuning — this module never
auto-adjusts a floor; it only computes the rates so a maintainer can tighten on false-approval (never
loosen on false-rejection), for **either** jury.

Definitions (``eval.md §7.5``):

- **false-approval**: the jury approved an artifact the human reference marks as needing revision.
  Costlier — bad foundations cascade.
- **false-rejection**: the jury demanded revisions on an artifact the human reference marks as
  acceptable. Costs cycles, doesn't corrupt outputs.
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


class JuryKind(StrEnum):
    """Which jury produced the verdict being reviewed (ADR 0102).

    Phase 1 had only the Extractor-Jury; Phase 2 adds the Planner-Jury. The two are calibrated
    independently (``implementation-plan.md §4.4``/§5.4) but under the same asymmetric discipline.
    """

    EXTRACTOR = "extractor"
    PLANNER = "planner"


class JuryReviewEntry(ArtifactModel):
    """One reviewed jury decision (``eval.md §7.5``).

    ``blog_id`` + ``run_index`` locate the run; ``jury`` is which jury produced the verdict;
    ``mark`` is the maintainer's judgment; ``note`` optionally records why (e.g., "jury approved but
    step 3's citation points at the wrong passage"). ``jury`` defaults to ``EXTRACTOR`` so any
    pre-ADR-0102 ledger (Extractor-Jury only) forward-loads unchanged.
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    run_index: int = Field(ge=0)
    jury: JuryKind = JuryKind.EXTRACTOR
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


class JuryRates(ArtifactModel):
    """Per-jury false-approval / false-rejection rates across all reviewed blogs (ADR 0102).

    The unit the asymmetric calibration (``CALIBRATION.md``) acts on: each jury's floor is tuned
    *upward* on its observed false-approval rate, never loosened on false-rejection.
    """

    model_config = ConfigDict(frozen=True)

    jury: JuryKind
    reviewed: int = Field(ge=0)
    false_approvals: int = Field(ge=0)
    false_rejections: int = Field(ge=0)

    @property
    def false_approval_rate(self) -> float:
        """Share of this jury's reviewed verdicts that were false approvals (0 when none)."""
        return self.false_approvals / self.reviewed if self.reviewed else 0.0

    @property
    def false_rejection_rate(self) -> float:
        """Share of this jury's reviewed verdicts that were false rejections (0 when none)."""
        return self.false_rejections / self.reviewed if self.reviewed else 0.0


class JuryReviewLedger(ArtifactModel):
    """The maintainer's reviewed jury decisions + the aggregated rates (ADR 0025/0102).

    ``ArtifactModel`` because the ledger is archived alongside eval reports
    (``eval.md §7.13``) and round-trips through YAML. Entries are added via
    :meth:`with_mark` (frozen-friendly: returns a new ledger) so the model stays
    immutable. Aggregations accept an optional ``jury`` filter; with no filter they cover **all**
    juries (so a Phase-1 extract-only ledger reads exactly as before).
    """

    model_config = ConfigDict(frozen=True)

    spec_version: int = 1
    spec_kind: str = "JuryReviewLedger"
    rotation_generation: int = Field(ge=0)
    entries: list[JuryReviewEntry] = Field(default_factory=list[JuryReviewEntry])

    def with_mark(
        self,
        *,
        blog_id: str,
        run_index: int,
        mark: ReviewMark,
        jury: JuryKind = JuryKind.EXTRACTOR,
        note: str | None = None,
    ) -> JuryReviewLedger:
        """Return a new ledger with one more reviewed decision appended."""
        entry = JuryReviewEntry(
            blog_id=blog_id, run_index=run_index, jury=jury, mark=mark, note=note
        )
        return self.model_copy(update={"entries": [*self.entries, entry]})

    def _for_jury(self, jury: JuryKind | None) -> list[JuryReviewEntry]:
        """Entries for ``jury`` (or all entries when ``jury is None``)."""
        if jury is None:
            return self.entries
        return [e for e in self.entries if e.jury is jury]

    def per_blog_rates(self, *, jury: JuryKind | None = None) -> list[BlogReviewRates]:
        """Per-blog false-approval / false-rejection rates (``eval.md §7.5``).

        ``jury`` (default ``None`` = all juries) restricts the aggregation to one jury's verdicts.
        """
        by_blog: dict[str, list[JuryReviewEntry]] = {}
        for e in self._for_jury(jury):
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

    def per_jury_rates(self) -> list[JuryRates]:
        """Per-jury false-approval / false-rejection rates — the calibration unit (ADR 0102).

        One :class:`JuryRates` per jury that has at least one reviewed verdict, in
        :class:`JuryKind` declaration order so output is deterministic run to run.
        """
        out: list[JuryRates] = []
        for jury in JuryKind:
            items = self._for_jury(jury)
            if not items:
                continue
            out.append(
                JuryRates(
                    jury=jury,
                    reviewed=len(items),
                    false_approvals=sum(1 for i in items if i.mark is ReviewMark.FALSE_APPROVAL),
                    false_rejections=sum(1 for i in items if i.mark is ReviewMark.FALSE_REJECTION),
                )
            )
        return out

    def overall_false_approval_rate(self, *, jury: JuryKind | None = None) -> float:
        """False-approval rate across reviewed verdicts (``eval.md §7.5``); optional ``jury`` filter."""
        items = self._for_jury(jury)
        if not items:
            return 0.0
        return sum(1 for e in items if e.mark is ReviewMark.FALSE_APPROVAL) / len(items)

    def overall_false_rejection_rate(self, *, jury: JuryKind | None = None) -> float:
        """False-rejection rate across reviewed verdicts (``eval.md §7.5``); optional ``jury`` filter."""
        items = self._for_jury(jury)
        if not items:
            return 0.0
        return sum(1 for e in items if e.mark is ReviewMark.FALSE_REJECTION) / len(items)


__all__ = [
    "BlogReviewRates",
    "JuryKind",
    "JuryRates",
    "JuryReviewEntry",
    "JuryReviewLedger",
    "ReviewMark",
]
