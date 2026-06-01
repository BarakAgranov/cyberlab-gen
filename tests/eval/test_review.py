"""Tests for the manual jury-decision review tooling (``eval.md §7.5``, ADR 0025).

The tool must aggregate per-blog and overall false-approval / false-rejection
rates from maintainer marks. These rates are the only honest signal on jury
overconfidence (``implementation-plan.md §4.6``), so the arithmetic must be exact.
"""

from __future__ import annotations

import pytest

from eval.runner.review import JuryReviewLedger, ReviewMark


def _ledger() -> JuryReviewLedger:
    return JuryReviewLedger(rotation_generation=0)


def test_with_mark_is_immutable_and_appends() -> None:
    base = _ledger()
    one = base.with_mark(blog_id="b", run_index=0, mark=ReviewMark.CORRECT)
    assert base.entries == []  # original unchanged (frozen model)
    assert len(one.entries) == 1


def test_per_blog_rates() -> None:
    ledger = (
        _ledger()
        .with_mark(blog_id="b1", run_index=0, mark=ReviewMark.CORRECT)
        .with_mark(blog_id="b1", run_index=1, mark=ReviewMark.FALSE_APPROVAL)
        .with_mark(blog_id="b1", run_index=2, mark=ReviewMark.FALSE_REJECTION)
        .with_mark(blog_id="b2", run_index=0, mark=ReviewMark.FALSE_APPROVAL)
        .with_mark(blog_id="b2", run_index=1, mark=ReviewMark.FALSE_APPROVAL)
    )
    rates = {r.blog_id: r for r in ledger.per_blog_rates()}
    assert rates["b1"].reviewed == 3
    assert abs(rates["b1"].false_approval_rate - 1 / 3) < 1e-9
    assert abs(rates["b1"].false_rejection_rate - 1 / 3) < 1e-9
    assert rates["b2"].false_approval_rate == 1.0
    assert rates["b2"].false_rejection_rate == 0.0


def test_overall_rates() -> None:
    ledger = (
        _ledger()
        .with_mark(blog_id="b", run_index=0, mark=ReviewMark.CORRECT)
        .with_mark(blog_id="b", run_index=1, mark=ReviewMark.FALSE_APPROVAL)
        .with_mark(blog_id="b", run_index=2, mark=ReviewMark.FALSE_REJECTION)
        .with_mark(blog_id="b", run_index=3, mark=ReviewMark.CORRECT)
    )
    assert ledger.overall_false_approval_rate() == 0.25
    assert ledger.overall_false_rejection_rate() == 0.25


def test_empty_ledger_rates_are_zero_not_div0() -> None:
    ledger = _ledger()
    assert ledger.overall_false_approval_rate() == 0.0
    assert ledger.overall_false_rejection_rate() == 0.0
    assert ledger.per_blog_rates() == []


def test_ledger_round_trips_through_yaml() -> None:
    ledger = _ledger().with_mark(
        blog_id="b", run_index=0, mark=ReviewMark.FALSE_APPROVAL, note="step 3 citation wrong"
    )
    again = JuryReviewLedger.from_yaml(ledger.to_yaml())
    assert again == ledger


def test_unknown_mark_rejected() -> None:
    from pydantic import ValidationError

    from eval.runner.review import JuryReviewEntry

    with pytest.raises(ValidationError):
        JuryReviewEntry.model_validate({"blog_id": "b", "run_index": 0, "mark": "maybe_ok"})
