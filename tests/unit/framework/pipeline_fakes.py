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
from cyberlab_gen.agents.results import ExtractionResult
from cyberlab_gen.registries.merge import load_merged_registries
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    ExtractionMetadataBlock,
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
from cyberlab_gen.schemas.ingestion import IngestionResult
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
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
    loop all the way to the global iteration cap / LangGraph recursion_limit (L3).
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
