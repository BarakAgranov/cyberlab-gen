"""Shared builders + a fake runner for the Phase-1 eval-harness tests (ADR 0025).

The fakes implement the narrow ``EvalPipelineRunner`` surface the harness depends
on, returning scripted ``BlogRunRecord``s so the metric/aggregation/archive/review
logic is exercised deterministically with no live provider (``eval.md §7.2``).
The spec builder mirrors the one in ``tests/unit/framework/test_orchestrator.py``
so the structural-completeness metric is computed against a realistic AttackSpec.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    ExtractionMetadataBlock,
    ExtrasEntry,
    PerStepReproducibility,
    PublisherBlock,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityTier,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from eval.runner.runner import EvalPipelineRunner, record_from_run

if TYPE_CHECKING:
    from eval.runner.metrics import BlogRunRecord

_HASH = "a" * 64


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def make_spec(
    *,
    completeness: float = 0.8,
    extras: int = 0,
    in_scope: bool = True,
) -> AttackSpec:
    """Build a representative in-scope (or out-of-scope) AttackSpec for metric tests."""
    source = SourceBlock(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        title="A writeup",
        publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
        fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
        content_hash=_HASH,
        fetch_method="httpx",
        word_count=100,
    )
    meta = ExtractionMetadataBlock(
        extractor_version="1.0.0", model="m", completeness_score=completeness, citations_count=2
    )
    extras_list = [
        ExtrasEntry(name=f"x{i}", description=_pstr("note"), source=ProvenanceSource.BLOG_EXPLICIT)
        for i in range(extras)
    ]
    if not in_scope:
        return AttackSpec(
            spec_version=1,
            source=source,
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="The blog is a product announcement with no attack chain.",
            extraction_metadata=meta,
            extras=extras_list,
        )
    return AttackSpec(
        spec_version=1,
        source=source,
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=ThesisBlock(
            types=["vulnerability_chain"],  # type: ignore[list-item]
            summary=_pstr("a chain"),
            attacker_objective=_pstr("admin"),
            vulnerability_story=_pstr("misconfig"),
            duration_as_described=_pstr("a week"),
        ),
        facets=["target:aws"],  # type: ignore[list-item]
        chain=ChainBlock(
            chain_steps=[
                ChainStep(
                    id="step-1",  # type: ignore[arg-type]
                    step_number=1,
                    title="Step 1",
                    description=_pstr("do the thing"),
                    blog_excerpt="verbatim",
                    techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
                    reproducibility=PerStepReproducibility(
                        classification=ReproducibilityTier.FULL,
                        caveats=_pstr("none"),
                        why=_pstr("scriptable"),
                    ),
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                )
            ]
        ),
        extraction_metadata=meta,
        extras=extras_list,
    )


def make_record(
    blog_id: str,
    run_index: int,
    *,
    layer1_passed: bool = True,
    shipped: bool = True,
    completeness: float = 0.8,
    extras: int = 0,
    cost: str = "0.01",
    vt_proposals: int = 0,
    facet_proposals: int = 0,
    verdict: Verdict = Verdict.APPROVE,
) -> BlogRunRecord:
    """Build a ``BlogRunRecord`` via the production ``record_from_run`` mapping."""
    return record_from_run(
        blog_id=blog_id,
        run_index=run_index,
        shipped=shipped,
        layer1_passed=layer1_passed,
        cost_usd=Decimal(cost),
        spec=make_spec(completeness=completeness, extras=extras),
        value_type_proposals=vt_proposals,
        facet_proposals=facet_proposals,
        verdict=verdict,
    )


def make_failure_record(
    blog_id: str,
    run_index: int,
    *,
    failure_kind: str,
    halt_reason: str,
    cost: str = "0",
) -> BlogRunRecord:
    """Build a non-shipped :class:`BlogRunRecord` for fail-fast/cost-cap tests."""
    from eval.runner.metrics import BlogRunRecord

    return BlogRunRecord(
        blog_id=blog_id,
        run_index=run_index,
        shipped=False,
        layer1_passed=False,
        cost_usd=Decimal(cost),
        completeness_score=0.0,
        structural_completeness=0.0,
        value_type_proposals=0,
        facet_proposals=0,
        extras_count=0,
        verdict=Verdict.REJECT,
        halt_reason=halt_reason,
        failure_kind=failure_kind,
    )


class FakeEvalRunner(EvalPipelineRunner):
    """A scripted ``EvalPipelineRunner``: returns canned records per (blog, run).

    ``records_for`` maps a blog id to the list of records its N runs should yield;
    if a blog id is absent, a default healthy record is synthesized. ``calls``
    records every (blog_id, run_index) so tests can assert the N-runs-per-blog
    invocation pattern.
    """

    def __init__(self, records_for: dict[str, list[BlogRunRecord]] | None = None) -> None:
        self._records_for = records_for or {}
        self.calls: list[tuple[str, int]] = []

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:
        self.calls.append((blog_id, run_index))
        scripted = self._records_for.get(blog_id)
        if scripted is not None and run_index < len(scripted):
            return scripted[run_index]
        return make_record(blog_id, run_index)
