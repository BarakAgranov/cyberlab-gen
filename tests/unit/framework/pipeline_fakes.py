"""Shared, typed pipeline test doubles + builders.

These fakes implement the narrow ``extract`` / ``refine`` / ``review`` surfaces the
orchestrator (and the ``PipelineExtractRunner``) depend on, recording how they were
called so the retry-vs-refinement *path* can be asserted directly. They live here —
public and fully typed — so the orchestrator, checkpointing, CLI-persistence, and eval
tests all drive the same fixture spec rather than each re-deriving it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor_jury.jury import DEFAULT_RUBRIC_FLOOR
from cyberlab_gen.agents.extractor_jury.schema import (
    JuryFieldFeedback,
    JuryScores,
    JuryVerdict,
    Verdict,
)
from cyberlab_gen.agents.results import (
    ExtractionResult,
    PlanAttempt,
    PlannerRefusal,
    PlanOutcome,
    PlanResult,
)
from cyberlab_gen.registries.merge import load_merged_registries
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    ExtractionMetadataBlock,
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
from cyberlab_gen.schemas.ingestion import IngestionResult
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
from cyberlab_gen.validators.semantic_cross_check_validator import (
    SemanticCrossCheckCode,
    SemanticCrossCheckFinding,
    SemanticCrossCheckResult,
)
from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

if TYPE_CHECKING:
    import pytest

    from cyberlab_gen.validators.grounding_validator import GroundingFinding

HASH = "a" * 64


# --- builders --------------------------------------------------------------


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])


def make_spec(*, facets: list[str] | None = None) -> AttackSpec:
    """An in-scope, structurally-valid AttackSpec fixture (optionally with ``facets``)."""
    return AttackSpec(
        spec_version=1,
        source=SourceBlock(
            url="https://example.com/blog",  # type: ignore[arg-type]
            canonical_url="https://example.com/blog",  # type: ignore[arg-type]
            title="A writeup",
            publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
            fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
            content_hash=HASH,
            fetch_method="httpx",
            word_count=100,
        ),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=ThesisBlock(
            types=["vulnerability_chain"],  # type: ignore[list-item]
            summary=_pstr("a chain"),
            attacker_objective=_pstr("admin"),
            vulnerability_story=_pstr("misconfig"),
            duration_as_described=_pstr("a week"),
        ),
        facets=facets or [],  # type: ignore[arg-type]
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
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0", model="m", completeness_score=0.8, citations_count=2
        ),
    )


def make_result(spec: AttackSpec) -> ExtractionResult:
    """Wrap a spec in an empty-proposals ``ExtractionResult`` envelope."""
    return ExtractionResult(
        attack_spec=spec,
        value_type_proposals=[],
        facet_proposals=[],
        thesis_type_proposals=[],
        lookups=[],
    )


def make_verdict(
    verdict: Verdict,
    *,
    feedback: list[JuryFieldFeedback] | None = None,
    scores: JuryScores | None = None,
) -> JuryVerdict:
    """A jury verdict with the given ``feedback`` and ``scores``.

    ``scores`` defaults to uniform 0.9 (above any sane floor); pass an explicit
    ``JuryScores`` to exercise the framework's rubric-floor backstop (ADR 0067) — e.g. an
    ``approve`` with a sub-floor dimension, the self-contradiction that must not ship.
    """
    return JuryVerdict(
        verdict=verdict,
        scores=scores
        or JuryScores(
            fidelity=0.9, completeness=0.9, provenance_correctness=0.9, structural_validity=0.9
        ),
        feedback=feedback or [],
        retry_recommended=verdict is not Verdict.APPROVE,
        rationale="because",
    )


def make_ingestion() -> IngestionResult:
    """A resolved ``IngestionResult`` for tests that stub the fetch."""
    return IngestionResult(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        content_hash=HASH,
        fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
        fetch_method="http_get",
        word_count=100,
        publisher_domain="example.com",
        cached_path="/tmp/cache/x",
    )


def make_validator() -> StaticSchemaValidator:
    """The real static-schema validator over the bundled registries."""
    return StaticSchemaValidator(registries=load_merged_registries())


# --- LabManifest builder ---------------------------------------------------


def _sev(value: Severity) -> Provenance[Severity]:
    return Provenance[Severity](
        value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()]
    )


def _mstep(num: int, tier: ReproducibilityTier) -> StepBlock:
    return StepBlock(
        id=f"step-{num}",  # type: ignore[arg-type]
        step_number=num,
        title=f"Step {num}",
        description=_pstr("do the thing"),
        function_name=f"step_{num}",
        reproducibility=PerStepReproducibility(
            classification=tier,
            caveats=_pstr("carried forward from the chain step"),
            why=_pstr("authored by the Extractor; the Planner does not re-evaluate"),
        ),
    )


def _phase(num: int, tier: ReproducibilityTier) -> PhaseBlock:
    """A skeleton phase — one step carrying ``tier``, NO ``implementation.path`` (ADR 0079)."""
    return PhaseBlock(
        id=f"phase-{num}",  # type: ignore[arg-type]
        name=f"Phase {num}",
        display_name=f"{num}. Phase",
        short_description="Phase description.",
        step_composition=StepComposition.SEQUENTIAL,
        execution_context="attacker_local",  # type: ignore[arg-type]
        provisioning_mechanism=ProvisioningMechanism.CLI_SCRIPTS,
        steps=[_mstep(num, tier)],
        implementation=PhaseImplementation(language="python"),  # path=None: skeleton, no code yet
    )


def make_manifest(*, step_tiers: list[ReproducibilityTier] | None = None) -> LabManifest:
    """A representative draft ``LabManifest`` skeleton — a canned Planner output for tests.

    Two skeleton phases (no ``implementation.path``); a multi-role lab_resource; both
    ``identifier_kind`` values; a ``cli_flag_or_default`` input; a ``manual`` pre_lab prereq; an
    IaC output. ``step_tiers`` sets each phase's per-step reproducibility tier (default
    ``[full, demonstration_only]``). ``core.reproducibility`` is deliberately a **stand-in**
    (``full`` with prose) so a test can prove ``Planner.plan`` overwrites it with the
    framework-derived value (ADR 0090).
    """
    tiers = step_tiers or [ReproducibilityTier.FULL, ReproducibilityTier.DEMONSTRATION_ONLY]
    return LabManifest(
        spec_version=1,
        spec_kind=SpecKind.LAB_MANIFEST,
        core=CoreBlock(
            id="codebuild-lab",  # type: ignore[arg-type]
            name="CodeBuild Lab",
            source=SourceBlock(
                url="https://example.com/blog",  # type: ignore[arg-type]
                canonical_url="https://example.com/blog",  # type: ignore[arg-type]
                title="A writeup",
                publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
                fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
                content_hash=HASH,
                fetch_method="httpx",
                word_count=100,
            ),
            thesis=_pstr("a supply-chain attack via CodeBuild"),
            severity=_sev(Severity.HIGH),
            reproducibility=ReproducibilityBlock(
                classification_lab_level=ReproducibilityLabLevel.FULL,
                overall_assessment=_pstr("the LLM's stand-in prose; the framework overwrites this"),
            ),
            generation=GenerationBlock(
                tool_version="1.0.0",  # type: ignore[arg-type]
                model="claude-opus-4-8",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        facets=["target:aws"],  # type: ignore[list-item]
        prereqs=PrereqsBlock(
            pre_lab=[
                PrereqBlock(
                    id="aws-creds",  # type: ignore[arg-type]
                    description="AWS credentials configured for the lab account",
                    kind=PrereqKind.MANUAL,
                    timing=PrereqTiming.PRE_LAB,
                ),
            ],
        ),
        inputs=[
            InputBlock(
                name="target_region",  # type: ignore[arg-type]
                type="aws_region",  # type: ignore[arg-type]
                source=InputSource.CLI_FLAG_OR_DEFAULT,
                default="us-east-1",
            ),
        ],
        lab_resources=[
            LabResourceBlock(
                id="logging-bucket",  # type: ignore[arg-type]
                type="aws_s3_bucket",  # type: ignore[arg-type]
                intended_iac_resource_type="aws_s3_bucket",
                provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                lab_role=[LabRole.DEFENDER_INFRASTRUCTURE, LabRole.ATTACK_TARGET],
                description=_pstr("logging bucket the attack deletes from to cover tracks"),
            ),
        ],
        phases=[_phase(i + 1, tier) for i, tier in enumerate(tiers)],
        outputs=[
            OutputBlock(
                name="bucket_name",  # type: ignore[arg-type]
                type="aws_s3_bucket",  # type: ignore[arg-type]
                iac_reference="terraform.output.bucket_name",
            ),
        ],
    )


def make_plan_attempt(*, step_tiers: list[ReproducibilityTier] | None = None) -> PlanAttempt:
    """A ``planned`` PlanAttempt wrapping :func:`make_manifest` — the Planner's forced output."""
    return PlanAttempt(outcome=PlanOutcome.PLANNED, manifest=make_manifest(step_tiers=step_tiers))


def make_route_back_attempt(
    *, field_paths: list[str] | None = None, summary: str = "step preconditions do not match"
) -> PlanAttempt:
    """An ``attackspec_incoherent`` PlanAttempt — the Planner surfaces a defect it may not repair.

    Drives the route-back-to-Extractor path: the Planner flags AttackSpec incoherence with structured
    detail and produces no manifest (``agents.md §5.7``).
    """
    return PlanAttempt(
        outcome=PlanOutcome.ATTACKSPEC_INCOHERENT,
        refusal=PlannerRefusal(
            summary=summary,  # type: ignore[arg-type]
            attack_spec_field_paths=field_paths or ["chain.chain_steps[1].description"],  # type: ignore[arg-type]
            detail="step 2 assumes credentials step 1 never establishes",  # type: ignore[arg-type]
        ),
    )


def make_cannot_plan_attempt(
    *, summary: str = "the AttackSpec has no plannable chain"
) -> PlanAttempt:
    """A ``cannot_plan`` PlanAttempt — gaps too large to plan around; the run halts."""
    return PlanAttempt(
        outcome=PlanOutcome.CANNOT_PLAN,
        refusal=PlannerRefusal(
            summary=summary,  # type: ignore[arg-type]
            attack_spec_field_paths=["chain.chain_steps"],  # type: ignore[arg-type]
            detail="every chain step is not_reproducible; no lab content remains",  # type: ignore[arg-type]
        ),
    )


# --- the agent fakes -------------------------------------------------------


class FakeExtractor:
    """Records extract() and refine() calls and returns scripted specs in sequence.

    A jury ``revise`` routes to :meth:`refine` (targeted patch); a structural retry and
    the first run route to :meth:`extract`. By default ``refine`` echoes the prior spec
    (a no-op, valid patch); pass ``refine_specs`` to script distinct patched outputs.
    """

    def __init__(
        self, specs: list[AttackSpec], *, refine_specs: list[AttackSpec] | None = None
    ) -> None:
        self._specs = specs
        self._refine_specs = refine_specs
        self.calls: list[str] = []  # the source_summary each extract() call received
        self.refine_calls: list[tuple[AttackSpec, list[JuryFieldFeedback]]] = []

    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult:
        del blog_content
        self.calls.append(source_summary)
        idx = min(len(self.calls) - 1, len(self._specs) - 1)
        return make_result(self._specs[idx])

    async def refine(
        self,
        *,
        prior_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        blog_content: str,
        source_summary: str,
    ) -> ExtractionResult:
        del blog_content, source_summary
        self.refine_calls.append((prior_spec, list(feedback)))
        if self._refine_specs is not None:
            idx = min(len(self.refine_calls) - 1, len(self._refine_specs) - 1)
            return make_result(self._refine_specs[idx])
        return make_result(prior_spec)  # default: echo the prior spec (a no-op, valid patch)


class ChangingBadExtractor:
    """Emits a DIFFERENT static-schema-invalid spec on every extract.

    Each run carries a distinct unknown facet, so the finding set keeps changing and the
    orchestrator's no-progress early-bail (ADR 0057) never fires — used to drive a runaway
    loop all the way to the global iteration cap / LangGraph recursion_limit.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.refine_calls: list[tuple[AttackSpec, list[JuryFieldFeedback]]] = []

    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult:
        del blog_content
        self.calls.append(source_summary)
        return make_result(make_spec(facets=[f"target:bogus_unknown_{len(self.calls)}"]))

    async def refine(
        self,
        *,
        prior_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        blog_content: str,
        source_summary: str,
    ) -> ExtractionResult:
        del blog_content, source_summary
        self.refine_calls.append((prior_spec, list(feedback)))
        return make_result(prior_spec)


class FakeJury:
    """Records every review() call and returns scripted verdicts in sequence.

    ``reviewed_specs`` and ``reviewed_findings`` record what the jury *saw* at review
    time, so a test can assert the jury consumes the orchestrator's grounding findings
    (ADR 0051/0060) and reviews the enriched spec (ADR 0052).
    """

    def __init__(
        self, verdicts: list[JuryVerdict], *, rubric_floor: float = DEFAULT_RUBRIC_FLOOR
    ) -> None:
        self._verdicts = verdicts
        self.calls = 0
        self.rubric_floor = rubric_floor
        self.reviewed_specs: list[AttackSpec] = []
        self.reviewed_findings: list[list[GroundingFinding] | None] = []

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        grounding_findings: list[GroundingFinding] | None = None,
    ) -> JuryVerdict:
        del blog_content
        self.reviewed_specs.append(spec)
        self.reviewed_findings.append(grounding_findings)
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]


class CrashOnceJury:
    """Raises on its first review (a mid-node crash), then returns scripted verdicts."""

    def __init__(
        self, verdicts: list[JuryVerdict], *, rubric_floor: float = DEFAULT_RUBRIC_FLOOR
    ) -> None:
        self._verdicts = verdicts
        self.calls = 0
        self.rubric_floor = rubric_floor

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        grounding_findings: list[GroundingFinding] | None = None,
    ) -> JuryVerdict:
        del spec, blog_content, grounding_findings
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom in jury")
        return self._verdicts[min(self.calls - 2, len(self._verdicts) - 1)]


# --- plan-pipeline result builders + agent fakes ---------------------------


def make_plan_result(manifest: LabManifest | None = None) -> PlanResult:
    """A ``planned`` PlanResult wrapping a finalized manifest (the Planner's success envelope)."""
    return PlanResult(outcome=PlanOutcome.PLANNED, manifest=manifest or make_manifest(), lookups=[])


def make_route_back_result() -> PlanResult:
    """An ``attackspec_incoherent`` PlanResult — drives the route-back-to-Extractor decision."""
    attempt = make_route_back_attempt()
    return PlanResult(outcome=attempt.outcome, refusal=attempt.refusal, lookups=[])


def make_cannot_plan_result() -> PlanResult:
    """A ``cannot_plan`` PlanResult — drives the halt-with-gap-report decision."""
    attempt = make_cannot_plan_attempt()
    return PlanResult(outcome=attempt.outcome, refusal=attempt.refusal, lookups=[])


class FakePlanner:
    """Records plan()/refine() calls and returns scripted PlanResults in sequence.

    The coordinator routes on ``PlanResult.outcome``; a jury ``revise`` routes to :meth:`refine`,
    the first run to :meth:`plan`. By default ``refine`` echoes the prior manifest as a ``planned``
    result (a no-op patch); pass ``refine_results`` to script distinct patched outputs.
    """

    def __init__(
        self,
        plan_results: list[PlanResult],
        *,
        refine_results: list[PlanResult] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._plan_results = plan_results
        self._refine_results = refine_results
        self._raises = raises  # if set, plan() raises it (a provider failure, e.g. ToolLoopError)
        self.plan_calls = 0
        self.refine_calls: list[tuple[LabManifest, list[JuryFieldFeedback]]] = []

    async def plan(self, attack_spec: AttackSpec, *, preferences: str | None = None) -> PlanResult:
        del attack_spec, preferences
        self.plan_calls += 1
        if self._raises is not None:
            raise self._raises
        idx = min(self.plan_calls - 1, len(self._plan_results) - 1)
        return self._plan_results[idx]

    async def refine(
        self,
        *,
        prior_manifest: LabManifest,
        attack_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        preferences: str | None = None,
    ) -> PlanResult:
        del attack_spec, preferences
        self.refine_calls.append((prior_manifest, list(feedback)))
        if self._refine_results is not None:
            idx = min(len(self.refine_calls) - 1, len(self._refine_results) - 1)
            return self._refine_results[idx]
        return make_plan_result(prior_manifest)  # default: echo the prior manifest (no-op patch)


class FakePlannerJury:
    """Records every review() call and returns scripted verdicts in sequence.

    ``reviewed_manifests`` records what the jury *saw*, so a test can assert the jury reviews the
    patched manifest on a refinement iteration.
    """

    def __init__(self, verdicts: list[JuryVerdict], *, rubric_floor: float = 0.7) -> None:
        self._verdicts = verdicts
        self.calls = 0
        self.rubric_floor = rubric_floor
        self.reviewed_manifests: list[LabManifest] = []

    async def review(self, *, manifest: LabManifest, attack_spec: AttackSpec) -> JuryVerdict:
        del attack_spec
        self.reviewed_manifests.append(manifest)
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]


def make_cross_check_finding(
    *,
    code: SemanticCrossCheckCode = SemanticCrossCheckCode.MISSING_IMPLIED_FACET,
    location: str = "facets[0]",
    detail: str = "facet implies an undeclared facet the Planner must add",
) -> SemanticCrossCheckFinding:
    """One semantic-cross-check finding (defaults to a routable, Planner-owned MISSING_IMPLIED_FACET)."""
    return SemanticCrossCheckFinding(code=code, location=location, detail=detail)


def make_cross_check_result(
    findings: list[SemanticCrossCheckFinding] | None = None,
) -> SemanticCrossCheckResult:
    """A semantic-cross-check result; ``passed`` iff there are no findings."""
    items = findings or []
    return SemanticCrossCheckResult(passed=not items, findings=items)


class FakeCrossCheckValidator:
    """A scripted semantic-cross-check validator: returns queued results in sequence, default PASS.

    The plan graph's cross-check node depends only on ``validate(manifest) -> SemanticCrossCheckResult``;
    this records calls and returns queued results (the last repeats), so a test can drive a clean pass,
    a findings-then-pass refinement loop, or a persistent-findings halt.
    """

    def __init__(self, results: list[SemanticCrossCheckResult] | None = None) -> None:
        self._results = list(results) if results else [make_cross_check_result()]
        self.calls = 0

    def validate(self, manifest: LabManifest) -> SemanticCrossCheckResult:
        del manifest
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


# --- ingestion stub helper (typed, so callers avoid untyped monkeypatch lambdas) ---


def install_stub_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ingestion: IngestionResult,
    blog_content: str,
) -> None:
    """Patch ``framework.ingestion.ingest``/``read_cached_text`` to return fixtures.

    Lets the real ``PipelineExtractRunner.run`` reach the graph with no provider or
    network. Typed (not a lambda) so pyright-strict callers stay clean.
    """
    import cyberlab_gen.framework.ingestion as ingestion_mod

    def _ingest(_url: str, *, state: object = None) -> IngestionResult:
        del state
        return ingestion

    def _read_cached_text(_content_hash: str, *, state: object = None) -> str:
        del state
        return blog_content

    monkeypatch.setattr(ingestion_mod, "ingest", _ingest)
    monkeypatch.setattr(ingestion_mod, "read_cached_text", _read_cached_text)
