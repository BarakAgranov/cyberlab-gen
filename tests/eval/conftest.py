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
    CveReference,
    ExtractionMetadataBlock,
    ExtrasEntry,
    PerStepReproducibility,
    PublisherBlock,
    ReproducibilityBlock,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    InputSource,
    LabRole,
    PrereqKind,
    PrereqTiming,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityLabLevel,
    ReproducibilityTier,
    Severity,
    SpecKind,
    StepComposition,
)
from cyberlab_gen.schemas.manifest import (
    CoreBlock,
    GenerationBlock,
    InputBlock,
    LabManifest,
    LabResourceBlock,
    OutputBlock,
    PhaseBlock,
    PhaseImplementation,
    PrereqBlock,
    PrereqsBlock,
    StepBlock,
)
from cyberlab_gen.schemas.provenance import CitationBlock, Provenance, ProvenanceString
from eval.runner.plan_metrics import PlanRunRecord, record_from_plan_run
from eval.runner.runner import EvalPipelineRunner, record_from_run

if TYPE_CHECKING:
    from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus
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
    static_schema_passed: bool = True,
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
        static_schema_passed=static_schema_passed,
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
        static_schema_passed=False,
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


# --- Phase-2 plan-stage fixtures (ADR 0102) --------------------------------


def _sev(value: Severity) -> Provenance[Severity]:
    return Provenance[Severity](
        value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite_block()]
    )


def _cite_block() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _mstep(num: int, tier: ReproducibilityTier) -> StepBlock:
    return StepBlock(
        id=f"step-{num}",  # type: ignore[arg-type]
        step_number=num,
        title=f"Step {num}",
        description=_pstr("do the thing"),
        function_name=f"step_{num}",
        reproducibility=PerStepReproducibility(
            classification=tier, caveats=_pstr("none"), why=_pstr("carried forward")
        ),
    )


def _phase(num: int, tier: ReproducibilityTier) -> PhaseBlock:
    return PhaseBlock(
        id=f"phase-{num}",  # type: ignore[arg-type]
        name=f"Phase {num}",
        display_name=f"{num}. Phase",
        short_description="Phase description.",
        step_composition=StepComposition.SEQUENTIAL,
        execution_context="attacker_local",  # type: ignore[arg-type]
        provisioning_mechanism=ProvisioningMechanism.CLI_SCRIPTS,
        steps=[_mstep(num, tier)],
        implementation=PhaseImplementation(language="python"),
    )


def make_manifest(
    *,
    step_tiers: list[ReproducibilityTier] | None = None,
    lab_level: ReproducibilityLabLevel = ReproducibilityLabLevel.FULL,
    facets: bool = True,
    prereqs: bool = True,
    inputs: bool = True,
    lab_resources: bool = True,
    outputs: bool = True,
    mitre_tactics: bool = True,
    cve_references: bool = True,
) -> LabManifest:
    """A controllable ``LabManifest`` for plan-metric tests (ADR 0102).

    ``step_tiers`` (default ``[full]``) builds one phase per tier so the per-step reproducibility
    distribution is exact. ``lab_level`` is written straight onto ``core.reproducibility`` — the
    metric *reads* this emitted value (it never re-derives), so the test controls it directly. The
    booleans toggle each counted optional content collection so a test can drive
    ``manifest_field_coverage`` from 0.0 (all off) to 1.0 (all on).
    """
    tiers = step_tiers if step_tiers is not None else [ReproducibilityTier.FULL]
    return LabManifest(
        spec_version=1,
        spec_kind=SpecKind.LAB_MANIFEST,
        core=CoreBlock(
            id="metric-lab",  # type: ignore[arg-type]
            name="Metric Lab",
            source=SourceBlock(
                url="https://example.com/blog",  # type: ignore[arg-type]
                canonical_url="https://example.com/blog",  # type: ignore[arg-type]
                title="A writeup",
                publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
                fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
                content_hash=_HASH,
                fetch_method="httpx",
                word_count=100,
            ),
            mitre_tactics=["TA0001"] if mitre_tactics else [],  # type: ignore[list-item]
            thesis=_pstr("a chain"),
            severity=_sev(Severity.HIGH),
            cve_references=(
                [CveReference(cve_id="CVE-2026-0001", description=_pstr("a CVE"))]  # type: ignore[arg-type]
                if cve_references
                else []
            ),
            reproducibility=ReproducibilityBlock(classification_lab_level=lab_level),
            generation=GenerationBlock(
                tool_version="1.0.0",  # type: ignore[arg-type]
                model="m",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        facets=(["target:aws"] if facets else []),  # type: ignore[list-item]
        prereqs=(
            PrereqsBlock(
                pre_lab=[
                    PrereqBlock(
                        id="aws-creds",  # type: ignore[arg-type]
                        description="AWS credentials",
                        kind=PrereqKind.MANUAL,
                        timing=PrereqTiming.PRE_LAB,
                    )
                ]
            )
            if prereqs
            else PrereqsBlock()
        ),
        inputs=(
            [
                InputBlock(
                    name="target_region",  # type: ignore[arg-type]
                    type="aws_region",  # type: ignore[arg-type]
                    source=InputSource.CLI_FLAG_OR_DEFAULT,
                    default="us-east-1",
                )
            ]
            if inputs
            else []
        ),
        lab_resources=(
            [
                LabResourceBlock(
                    id="logging-bucket",  # type: ignore[arg-type]
                    type="aws_s3_bucket",  # type: ignore[arg-type]
                    intended_iac_resource_type="aws_s3_bucket",
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                    lab_role=[LabRole.ATTACK_TARGET],
                    description=_pstr("a bucket"),
                )
            ]
            if lab_resources
            else []
        ),
        phases=[_phase(i + 1, tier) for i, tier in enumerate(tiers)],
        outputs=(
            [
                OutputBlock(
                    name="bucket_name",  # type: ignore[arg-type]
                    type="aws_s3_bucket",  # type: ignore[arg-type]
                    iac_reference="terraform.output.bucket_name",
                )
            ]
            if outputs
            else []
        ),
    )


def make_plan_record(
    blog_id: str,
    run_index: int,
    *,
    status: PlanPipelineStatus | None = None,
    manifest: LabManifest | None = None,
    cost: str = "0.02",
    facet_proposals: int = 0,
    verdict: Verdict | None = Verdict.APPROVE,
    low_jury_confidence: bool = False,
    halt_reason: str | None = None,
    failure_kind: str | None = None,
    **manifest_kwargs: object,
) -> PlanRunRecord:
    """Build a :class:`PlanRunRecord` via the production ``record_from_plan_run`` mapping.

    Defaults to a clean ``PLANNED`` ship with a populated manifest (``make_manifest``). Pass
    ``status``/``manifest=None`` to script a non-ship; extra kwargs are forwarded to
    ``make_manifest`` so a test can vary coverage/tiers without building one by hand.
    """
    from decimal import Decimal

    from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus

    resolved_status = status if status is not None else PlanPipelineStatus.PLANNED
    shipped = resolved_status in (
        PlanPipelineStatus.PLANNED,
        PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
    )
    resolved_manifest = manifest
    if resolved_manifest is None and shipped:
        resolved_manifest = make_manifest(**manifest_kwargs)  # type: ignore[arg-type]
    return record_from_plan_run(
        blog_id=blog_id,
        run_index=run_index,
        status=resolved_status,
        cost_usd=Decimal(cost),
        manifest=resolved_manifest,
        facet_proposals=facet_proposals,
        verdict=verdict,
        low_jury_confidence=low_jury_confidence,
        halt_reason=halt_reason,
        failure_kind=failure_kind,
    )
