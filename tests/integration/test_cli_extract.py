"""Tests for the ``extract`` verb + post-Extractor interrupt (Phase 1 Task 7).

Covers the Task-7 exit criteria (``implementation-plan.md §4.5``, the brief):

* ``extract <url>`` on a fixture writes a valid ``attack-spec.yaml``;
* the four-option menu functions — each of Approve / Feedback / Edit / Abort is
  simulated;
* the per-proposal Accept/Edit menu functions and revalidates edits
  (a structurally-invalid edit reopens the editor with error comments);
* headless ``--interactive`` is rejected at startup pointing to ``--auto``;
* ``--auto`` runs without interrupts (and auto-accepts proposals up to the cap);
* the budget-overrun interrupt pauses in **both** modes.

The interrupt logic is the deliverable, so it is driven against a **fake**
``ExtractRunner`` (ADR 0024) returning a scripted ``RunResult`` — no live
provider, no cassettes. Menu choices arrive via ``CliRunner(input=...)``; the
``$EDITOR`` and the budget/editor unit paths are driven against ``run_extract``
directly with an injected ``editor`` callable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedValueType
from cyberlab_gen.cli import main as cli_main
from cyberlab_gen.cli.extract import (
    ATTACK_SPEC_FILENAME,
    DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP,
    RunResult,
    run_extract,
    spec_to_yaml,
)
from cyberlab_gen.cli.main import app
from cyberlab_gen.providers.cost_ledger import CostLedger
from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    ExtractionMetadataBlock,
    MaterialDiscrepancy,
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
from cyberlab_gen.schemas.registries import FacetEntry, ValueTypeEntry
from cyberlab_gen.state.run_store import (
    COST_FILENAME,
    ENRICHMENT_FILENAME,
    JURY_VERDICT_FILENAME,
    SPEC_FILENAME,
    RunRecord,
    RunStatus,
    RunStore,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from cyberlab_gen.state.local_state import LocalState

runner = CliRunner()

_HASH = "a" * 64


# --- builders --------------------------------------------------------------


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _in_scope_spec(*, facets: list[str] | None = None) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
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


def _out_of_scope_spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
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
        extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
        extraction_outcome_reason="pure on-prem AD attack with no cloud or supply-chain surface",
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0", model="m", completeness_score=0.1, citations_count=0
        ),
    )


def _proposal_vt() -> ProposedValueType:
    return ProposedValueType(name="s3_bucket_arn", description="an arn", reasoning="needed")


def _proposal_facet() -> ProposedFacet:
    return ProposedFacet(
        name="target:fastly",
        category="target",
        description="a CDN",
        applies_at_levels=["lab"],
        reasoning="the blog targets Fastly",
    )


# --- the fake runner (the ADR-0024 seam) -----------------------------------


class _FakeRunner:
    """A scripted ``ExtractRunner``: returns queued ``RunResult``s in order."""

    def __init__(self, results: list[RunResult]) -> None:
        self._results = results
        self.run_calls = 0
        self.feedback_calls: list[str] = []

    def run(self, url: str, *, ledger: CostLedger) -> RunResult:
        del url, ledger
        self.run_calls += 1
        return self._results[0]

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> RunResult:
        del ledger
        self.feedback_calls.append(feedback)
        idx = min(len(self.feedback_calls), len(self._results) - 1)
        return self._results[idx]


def _install_runner(monkeypatch: pytest.MonkeyPatch, runner_obj: _FakeRunner) -> None:
    def _factory(_state: LocalState) -> _FakeRunner:
        return runner_obj

    monkeypatch.setattr(cli_main, "extract_runner_factory", _factory, raising=True)


@pytest.fixture(autouse=True)
def _reset() -> None:  # pyright: ignore[reportUnusedFunction]
    cli_main.last_invocation_context = None
    cli_main.extract_runner_factory = None
    cli_main.stdin_tty_override = None


# --- CLI-driven menu tests (stdin-driven choices) --------------------------


def test_extract_auto_writes_valid_attack_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``extract <url> --auto`` writes a round-trippable ``attack-spec.yaml``."""
    _install_runner(
        monkeypatch, _FakeRunner([RunResult(spec=_in_scope_spec(facets=["target:aws"]))])
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["extract", "https://example.com/blog", "--auto"])
    assert result.exit_code == 0, result.output
    written = tmp_path / ATTACK_SPEC_FILENAME
    assert written.exists()
    # the file parses back into an equal AttackSpec (a real round-trip)
    from ruamel.yaml import YAML

    data = YAML().load(written.read_text(encoding="utf-8"))
    assert AttackSpec.model_validate(data) == _in_scope_spec(facets=["target:aws"])


def test_extract_interactive_approve_writes_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four-option menu: Approve writes the spec."""
    _install_runner(monkeypatch, _FakeRunner([RunResult(spec=_in_scope_spec())]))
    monkeypatch.chdir(tmp_path)
    # interactive needs a TTY; CliRunner provides stdin, so force the TTY check.
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    result = runner.invoke(app, ["extract", "https://example.com/blog"], input="a\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / ATTACK_SPEC_FILENAME).exists()


def test_extract_interactive_abort_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four-option menu: Abort writes no file and exits non-zero."""
    _install_runner(monkeypatch, _FakeRunner([RunResult(spec=_in_scope_spec())]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    result = runner.invoke(app, ["extract", "https://example.com/blog"], input="b\n")
    assert result.exit_code == 1
    assert not (tmp_path / ATTACK_SPEC_FILENAME).exists()
    assert "aborted" in result.output.lower()


def test_extract_interactive_feedback_reruns_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Option 2 (natural-language feedback) re-runs the Extractor, then Approve ships."""
    fake = _FakeRunner(
        [RunResult(spec=_in_scope_spec()), RunResult(spec=_in_scope_spec(facets=["target:aws"]))]
    )
    _install_runner(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # feedback choice -> feedback text -> approve
    result = runner.invoke(
        app, ["extract", "https://example.com/blog"], input="f\nfix the facets\na\n"
    )
    assert result.exit_code == 0, result.output
    assert fake.feedback_calls == ["fix the facets"]
    assert (tmp_path / ATTACK_SPEC_FILENAME).exists()


def test_extract_interactive_proposal_accept_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After Approve, the per-proposal Accept menu fires once per proposal and writes."""
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[_proposal_vt()],
        facet_proposals=[_proposal_facet()],
    )
    _install_runner(monkeypatch, _FakeRunner([rr]))
    state_dir = tmp_path / "state"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # approve artifact -> accept vt proposal -> accept facet proposal
    result = runner.invoke(
        app,
        ["--state-dir", str(state_dir), "extract", "https://example.com/blog"],
        input="a\na\na\n",
    )
    assert result.exit_code == 0, result.output
    assert "s3_bucket_arn" in result.output
    assert "target:fastly" in result.output
    assert (tmp_path / ATTACK_SPEC_FILENAME).exists()
    # Accept wrote both proposals into the overlay, marked human-approved (ADR 0044).
    overlay = state_dir / "registry-overlay"
    facets = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    vts = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    assert [e.name for e in facets.entries] == ["target:fastly"]
    assert [e.name for e in vts.entries] == ["s3_bucket_arn"]
    assert facets.proposals["target:fastly"].approval == "human"


# --- headless guard --------------------------------------------------------


def test_headless_interactive_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--interactive`` is rejected when stdin is not a TTY, pointing to ``--auto``."""
    _install_runner(monkeypatch, _FakeRunner([RunResult(spec=_in_scope_spec())]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", False)
    result = runner.invoke(app, ["extract", "https://example.com/blog", "--interactive"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "--auto" in combined
    assert not (tmp_path / ATTACK_SPEC_FILENAME).exists()


def test_extract_rejects_both_interactive_and_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_runner(monkeypatch, _FakeRunner([RunResult(spec=_in_scope_spec())]))
    result = runner.invoke(app, ["extract", "https://example.com/blog", "--interactive", "--auto"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "mutually exclusive" in combined


# --- out-of-scope ----------------------------------------------------------


def test_auto_out_of_scope_halts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--auto`` halts on out-of-scope content and writes nothing (``§3.1.1``)."""
    _install_runner(monkeypatch, _FakeRunner([RunResult(spec=_out_of_scope_spec())]))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["extract", "https://example.com/blog", "--auto"])
    assert result.exit_code == 1
    assert "out-of-scope" in result.output.lower()
    assert not (tmp_path / ATTACK_SPEC_FILENAME).exists()


# --- run-report-only material discrepancies (no interrupt in Phase 1) ------


def test_material_discrepancies_reported_not_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = MaterialDiscrepancy(
        field_path="external_references.cves[0].cvss_score",
        summary="blog said medium, NVD says critical",
        blog_value="5.0",
        authoritative_value="9.8",
        source_of_record="nvd",  # type: ignore[arg-type]
    )
    _install_runner(
        monkeypatch,
        _FakeRunner([RunResult(spec=_in_scope_spec(), material_discrepancies=[md])]),
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["extract", "https://example.com/blog", "--auto"])
    assert result.exit_code == 0, result.output
    # listed in the report; no per-discrepancy prompt happened (auto ran clean)
    assert "Material discrepancies" in result.output
    assert (tmp_path / ATTACK_SPEC_FILENAME).exists()


# --- run_extract-level tests: editor revalidation + budget overrun ---------


def _ledger(cap: str | None) -> CostLedger:
    # These verb-level tests exercise the SOFT everyday-budget predictive interrupt (ADR 0064),
    # so ``cap`` is the everyday budget. The $25 catastrophe ceiling (cap_usd) is the provider's
    # job and is not exercised here, so it is left unset.
    return CostLedger(
        run_id="t", cap_usd=None, everyday_budget_usd=Decimal(cap) if cap is not None else None
    )


def test_spec_edit_revalidates_and_reopens_on_invalid() -> None:
    """An artifact Edit with a structurally-invalid edit reopens with error comments."""
    from cyberlab_gen.cli.extract import (
        _edit_spec_with_revalidation,  # pyright: ignore[reportPrivateUsage]
    )

    spec = _in_scope_spec(facets=["target:aws"])
    # the valid edit drops a facet; the first edit is structurally broken (bad enum).
    broken = spec_to_yaml(spec).replace(
        "extraction_outcome: in_scope", "extraction_outcome: nonsense"
    )
    valid_edit = spec_to_yaml(_in_scope_spec(facets=["target:gcp"]))
    edits = iter([broken, valid_edit])
    seen: list[str] = []

    def editor(text: str) -> str:
        seen.append(text)
        return next(edits)

    edited = _edit_spec_with_revalidation(spec, editor=editor)
    # the second (valid) edit was accepted...
    assert edited.facets == ["target:gcp"]
    # ...and the editor was reopened with the structural-failure comment after the
    # first invalid edit (the comment is prepended to the reopened buffer).
    assert any("STRUCTURAL VALIDATION FAILED" in t for t in seen[1:])


def test_spec_edit_unchanged_keeps_original() -> None:
    """An editor that returns the text unchanged keeps the original spec."""
    from cyberlab_gen.cli.extract import (
        _edit_spec_with_revalidation,  # pyright: ignore[reportPrivateUsage]
    )

    spec = _in_scope_spec()
    edited = _edit_spec_with_revalidation(spec, editor=lambda _t: None)
    assert edited == spec


def test_proposal_edit_revalidates_and_reopens_on_invalid() -> None:
    """A per-proposal Edit revalidates; an invalid edit reopens with comments."""
    from cyberlab_gen.cli.extract import _review_one_proposal  # pyright: ignore[reportPrivateUsage]

    broken = "name: x\n"  # missing required `description` + `reasoning`
    valid = "name: edited_arn\ndescription: an arn\nreasoning: still needed\n"
    edits = iter([broken, valid])
    seen: list[str] = []

    def editor(text: str) -> str:
        seen.append(text)
        return next(edits)

    edited = _review_one_proposal(
        label="vt",
        model=_proposal_vt(),
        parse=ProposedValueType.model_validate,
        editor=editor,
        choice_reader=lambda: "e",  # force Edit
    )
    assert edited.name == "edited_arn"
    assert edited.reasoning == "still needed"
    assert any("STRUCTURAL VALIDATION FAILED" in t for t in seen[1:])


def test_spec_to_yaml_round_trips() -> None:
    """``spec_to_yaml`` output parses back to an equal AttackSpec."""
    from ruamel.yaml import YAML
    from ruamel.yaml.compat import StringIO

    spec = _in_scope_spec(facets=["target:aws"])
    text = spec_to_yaml(spec)
    data = YAML().load(StringIO(text))
    assert AttackSpec.model_validate(data) == spec


def test_budget_overrun_aborts_in_auto(tmp_path: Path) -> None:
    """Budget overrun in ``--auto`` aborts (no write) rather than overspending."""
    rr = RunResult(spec=_in_scope_spec(), estimated_next_stage_cost=Decimal("10"))
    fake = _FakeRunner([rr])
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger("1.00"),  # cap 1, est 10 → overrun
        stdin_is_tty=False,
        out_dir=tmp_path,
    )
    assert written is None
    assert not (tmp_path / ATTACK_SPEC_FILENAME).exists()


def test_budget_overrun_proceeds_in_auto_when_under_cap(tmp_path: Path) -> None:
    """Under the cap, ``--auto`` writes without a budget interrupt."""
    rr = RunResult(spec=_in_scope_spec(), estimated_next_stage_cost=Decimal("0.10"))
    fake = _FakeRunner([rr])
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger("5.00"),
        stdin_is_tty=False,
        out_dir=tmp_path,
    )
    assert written is not None
    assert written.exists()


def test_everyday_budget_trips_before_the_catastrophe_ceiling(tmp_path: Path) -> None:
    """ADR 0049/0064: the SOFT everyday budget ($10) trips the predictive interrupt with a real
    non-zero estimate, BEFORE and INDEPENDENT of the $25 ceiling — and --auto hard-stops on it."""
    ledger = CostLedger(run_id="t", cap_usd=Decimal("25"), everyday_budget_usd=Decimal("10"))
    # est 12: 0 + 12 > 10 (soft budget breached) but 12 < 25 (ceiling not reached).
    rr = RunResult(spec=_in_scope_spec(), estimated_next_stage_cost=Decimal("12"))
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=_FakeRunner([rr]),
        ledger=ledger,
        stdin_is_tty=False,
        out_dir=tmp_path,
    )
    assert written is None  # the soft budget hard-stopped --auto
    assert not (tmp_path / ATTACK_SPEC_FILENAME).exists()
    assert ledger.cap_usd == Decimal("25")  # the catastrophe ceiling is untouched + independent


def test_production_runner_sets_a_real_nonzero_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0064: the production runner sets the per-iteration estimate from the BILLED ledger
    (un-hardwiring the former zero), so the predictive everyday-budget interrupt can fire."""
    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.agents.extractor_jury.schema import JuryScores, JuryVerdict, Verdict
    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from cyberlab_gen.providers import (
        AgentLabel,
        CapabilityHint,
        ModelRankings,
        ProviderRegistry,
        TokenUsage,
    )
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
    from cyberlab_gen.providers.mock_provider import MockProvider
    from cyberlab_gen.registries.merge import load_merged_registries
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator
    from tests.unit.framework.pipeline_fakes import install_stub_ingestion, make_ingestion

    del tmp_path
    rankings = ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.LONG_CONTEXT_EXTRACTION.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ],
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ],
            }
        }
    )
    registry = ProviderRegistry(rankings, frozenset({"anthropic"}))
    registries = load_merged_registries()

    mock = MockProvider()
    mock.register(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        agent_label=AgentLabel.EXTRACTOR,
        response=_in_scope_spec(facets=["target:aws"]),
        usage=TokenUsage(input_tokens=1000, output_tokens=2000, cost_usd=Decimal("1.0")),
        model="claude-opus-4-8",
    )
    mock.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.EXTRACTOR_JURY,
        response=JuryVerdict(
            verdict=Verdict.APPROVE,
            scores=JuryScores(
                fidelity=0.9, completeness=0.9, provenance_correctness=0.9, structural_validity=0.9
            ),
            retry_recommended=False,
            rationale="clean",
        ),
        usage=TokenUsage(input_tokens=500, output_tokens=500, cost_usd=Decimal("0.5")),
        model="claude-opus-4-8",
    )

    ledger = CostLedger(run_id="t", cap_usd=Decimal("25"), everyday_budget_usd=Decimal("100"))
    crp = CostRecordingProvider(mock, ledger, purpose="test")
    install_stub_ingestion(monkeypatch, ingestion=make_ingestion(), blog_content="blog content")
    runner_obj = PipelineExtractRunner(
        extractor=Extractor(provider=crp, registry=registry, registries=registries),
        validator=StaticSchemaValidator(registries=registries),
        jury=ExtractorJury(provider=crp, registry=registry, registries=registries),
    )
    result = runner_obj.run("u", ledger=ledger)

    assert ledger.total_usd == Decimal("1.5")  # extract 1.0 + jury 0.5 billed into the ledger
    assert result.estimated_next_stage_cost == Decimal("1.5")  # the REAL estimate, not the old 0


def test_auto_over_cap_writes_up_to_cap_and_reports_remainder(tmp_path: Path) -> None:
    """Over the per-run cap, ``--auto`` SHIPS, promotes up to the cap, and reports the rest.

    ADR 0050/0062: over-cap is bounded steering, not a hard halt. The spec ships, exactly
    ``cap`` proposals are promoted to the overlay, and the remainder are reported (not dropped
    silently, not halted). Replaces the old ProposalCapExceeded behaviour.
    """
    overlay = tmp_path / "overlay"
    n = DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP + 2
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[
            ProposedValueType(name=f"vt_{i}", description="d", reasoning="r") for i in range(n)
        ],
    )
    fake = _FakeRunner([rr])
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger(None),
        stdin_is_tty=False,
        out_dir=tmp_path,
        overlay_dir=overlay,
    )
    assert written is not None and written.exists()  # the spec SHIPPED
    vts = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    assert (
        len(vts.entries) == DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP
    )  # exactly cap promoted, rest reported


def test_auto_accepts_under_cap_writes_overlay(tmp_path: Path) -> None:
    """Under the cap, ``--auto`` writes accepted proposals to the overlay (ADR 0044)."""
    overlay = tmp_path / "overlay"
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[_proposal_vt()],
        facet_proposals=[_proposal_facet()],
    )
    fake = _FakeRunner([rr])
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger(None),
        stdin_is_tty=False,
        out_dir=tmp_path,
        overlay_dir=overlay,
    )
    assert written is not None and written.exists()
    facets = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    vts = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    assert [e.name for e in facets.entries] == ["target:fastly"]
    assert [e.name for e in vts.entries] == ["s3_bucket_arn"]
    assert facets.proposals["target:fastly"].approval == "auto"
    assert facets.proposals["target:fastly"].source_lab is None


def test_auto_budget_abort_promotes_nothing(tmp_path: Path) -> None:
    """ADR 0050/0062: promotion is gated on ship — a budget abort in --auto writes no overlay."""
    overlay = tmp_path / "overlay"
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[_proposal_vt()],
        estimated_next_stage_cost=Decimal("10"),  # est 10 vs cap 1 -> overrun -> abort
    )
    fake = _FakeRunner([rr])
    written = run_extract(
        url="u",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger("1.00"),
        stdin_is_tty=False,
        out_dir=tmp_path,
        overlay_dir=overlay,
    )
    assert written is None
    assert not (overlay / "value_types.yaml").exists()  # nothing promoted (gated on ship)


def test_interactive_budget_abort_promotes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0050/0062: in --interactive, a budget abort AFTER per-proposal review writes no overlay.

    This is the orphan-write fix: the review used to write each accepted proposal to the overlay
    BEFORE the budget check, leaving overlay entries with no shipped spec on a budget abort.
    """
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[_proposal_vt()],
        estimated_next_stage_cost=Decimal("10"),
    )
    _install_runner(monkeypatch, _FakeRunner([rr]))
    state_dir = tmp_path / "state"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # approve artifact -> accept the vt proposal (review, no write) -> budget overrun -> abort
    result = runner.invoke(
        app,
        [
            "--max-llm-cost",
            "1",
            "--state-dir",
            str(state_dir),
            "extract",
            "https://example.com/blog",
        ],
        input="a\na\nb\n",
    )
    assert result.exit_code == 1, result.output
    overlay = state_dir / "registry-overlay"
    assert not (overlay / "value_types.yaml").exists()  # no orphan promotion on abort


# --- run-store persistence on every exit path (ADR 0039) -------------------


class _RaisingRunner:
    """An ``ExtractRunner`` whose ``run`` raises, optionally exposing a state.

    Models a pipeline that halted or was interrupted: ``last_state`` carries the
    (possibly partial) ``PipelineState`` the orchestrator produced before the halt
    error was raised, exactly as the production runner does.
    """

    def __init__(
        self,
        exc: BaseException,
        *,
        last_state: object | None = None,
        content_hash: str | None = None,
    ) -> None:
        self._exc = exc
        self.last_state = last_state
        self.content_hash = content_hash

    def run(self, url: str, *, ledger: CostLedger) -> RunResult:
        del url, ledger
        raise self._exc

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> RunResult:
        del feedback, ledger
        raise self._exc


def _only_run_dir(root: Path) -> Path:
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1, f"expected one run dir, found {dirs}"
    return dirs[0]


def _read_record(run_dir: Path) -> RunRecord:
    return RunRecord.model_validate_json((run_dir / "run.json").read_text(encoding="utf-8"))


def _partial_state(spec: AttackSpec) -> object:
    from cyberlab_gen.framework.orchestrator import PipelineState

    return PipelineState(blog_content="blog", source_summary="src", spec=spec)


def test_run_store_persists_complete_run_on_ship(tmp_path: Path) -> None:
    """A shipped run writes spec + cost + a finalized run.json, and keeps the cwd file."""
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    runs = tmp_path / "runs"
    fake = _FakeRunner([RunResult(spec=_in_scope_spec(facets=["target:aws"]))])

    written = run_extract(
        url="https://blog.example.com/posts/aws-attack",
        interactive=False,
        auto=True,
        runner=fake,
        ledger=_ledger(None),
        stdin_is_tty=False,
        out_dir=out_dir,
        run_store=RunStore(runs),
    )

    assert written == out_dir / ATTACK_SPEC_FILENAME  # cwd deliverable unchanged
    run_dir = _only_run_dir(runs)
    assert (run_dir / SPEC_FILENAME).is_file()
    assert (run_dir / COST_FILENAME).is_file()
    record = _read_record(run_dir)
    assert record.status is RunStatus.SHIPPED
    assert record.ended_at is not None
    assert record.lineage.input_ref == "https://blog.example.com/posts/aws-attack"
    assert "aws-attack" in run_dir.name  # readable, identifiable run id


def test_run_store_persists_partial_spec_on_jury_reject(tmp_path: Path) -> None:
    """A jury-reject halt still persists the produced (rejected) spec + a halt record."""
    from cyberlab_gen.framework.orchestrator import JuryRejectionError

    runs = tmp_path / "runs"
    fake = _RaisingRunner(
        JuryRejectionError("rejected for hallucination"),
        last_state=_partial_state(_in_scope_spec()),
    )

    with pytest.raises(JuryRejectionError):
        run_extract(
            url="u",
            interactive=False,
            auto=True,
            runner=fake,
            ledger=_ledger(None),
            stdin_is_tty=False,
            out_dir=tmp_path,
            run_store=RunStore(runs),
        )

    run_dir = _only_run_dir(runs)
    assert (run_dir / SPEC_FILENAME).is_file()  # the partial/rejected artifact is readable
    record = _read_record(run_dir)
    assert record.status is RunStatus.HALTED_REJECT
    assert record.halt_reason == "rejected for hallucination"


def test_run_store_persists_on_keyboard_interrupt(tmp_path: Path) -> None:
    """Ctrl-C mid-run finalizes the record as interrupted and re-raises."""
    runs = tmp_path / "runs"
    fake = _RaisingRunner(KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_extract(
            url="u",
            interactive=False,
            auto=True,
            runner=fake,
            ledger=_ledger(None),
            stdin_is_tty=False,
            out_dir=tmp_path,
            run_store=RunStore(runs),
        )

    record = _read_record(_only_run_dir(runs))
    assert record.status is RunStatus.INTERRUPTED


def test_run_store_persists_on_budget_exceeded(tmp_path: Path) -> None:
    """A mid-run catastrophe-ceiling abort persists a budget_exceeded record."""
    from cyberlab_gen.errors import BudgetExceeded

    runs = tmp_path / "runs"
    fake = _RaisingRunner(BudgetExceeded("ceiling crossed"))

    with pytest.raises(BudgetExceeded):
        run_extract(
            url="u",
            interactive=False,
            auto=True,
            runner=fake,
            ledger=_ledger("25"),
            stdin_is_tty=False,
            out_dir=tmp_path,
            run_store=RunStore(runs),
        )

    assert _read_record(_only_run_dir(runs)).status is RunStatus.BUDGET_EXCEEDED


def test_run_store_runs_do_not_overwrite(tmp_path: Path) -> None:
    """Two runs of the same URL produce two distinct, complete run directories."""
    runs = tmp_path / "runs"
    store = RunStore(runs)
    for _ in range(2):
        run_extract(
            url="https://blog.example.com/x",
            interactive=False,
            auto=True,
            runner=_FakeRunner([RunResult(spec=_in_scope_spec())]),
            ledger=_ledger(None),
            stdin_is_tty=False,
            out_dir=tmp_path,
            run_store=store,
        )
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert len(run_dirs) == 2


def test_persist_from_state_writes_all_stage_artifacts(tmp_path: Path) -> None:
    """``_persist_from_state`` writes spec, jury verdict and enrichment when present."""
    from cyberlab_gen.agents.extractor_jury.schema import (
        JuryScores,
        JuryVerdict,
        Verdict,
    )
    from cyberlab_gen.cli.extract import _persist_from_state  # pyright: ignore[reportPrivateUsage]
    from cyberlab_gen.framework.enrichment import EnrichmentResult
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.state.run_store import RunKind

    verdict = JuryVerdict(
        verdict=Verdict.APPROVE,
        scores=JuryScores(
            fidelity=1.0, completeness=1.0, provenance_correctness=1.0, structural_validity=1.0
        ),
        retry_recommended=False,
        rationale="looks good",
    )
    state = PipelineState(
        blog_content="b",
        source_summary="s",
        spec=_in_scope_spec(),
        verdict=verdict,
        enrichment=EnrichmentResult(),
    )
    handle = RunStore(tmp_path).start(kind=RunKind.EXTRACT, label="u")

    _persist_from_state(handle, state, _ledger(None))

    assert (handle.directory / SPEC_FILENAME).is_file()
    assert (handle.directory / JURY_VERDICT_FILENAME).is_file()
    assert (handle.directory / ENRICHMENT_FILENAME).is_file()
    # extractor_version is config/code provenance (legitimately spec-authored); model is
    # NOT — it is sourced from the billed ledger in _populate_lineage, never from the
    # LLM-authored extraction_metadata.model (architecture.md §1.5; investigation 0002 §7).
    # So _persist_from_state alone records the version and leaves model unset.
    assert handle.record.lineage.extractor_version == "1.0.0"
    assert handle.record.lineage.model is None


def test_run_store_lineage_populated_on_failed_run(tmp_path: Path) -> None:
    """Even a run that dies before emitting records model + input_hash (comparable runs)."""
    from cyberlab_gen.errors import MalformedOutput
    from cyberlab_gen.providers import (
        AgentLabel,
        CallOutcome,
        CapabilityHint,
        CostLedgerEntry,
        TokenUsage,
    )

    runs = tmp_path / "runs"
    ledger = _ledger(None)
    # A billed call happened before the failure (as in the real Wiz-blog run).
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime(2026, 6, 7, tzinfo=UTC),
            agent_label=AgentLabel.EXTRACTOR,
            provider="anthropic",
            model="claude-opus-4-8",
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            usage=TokenUsage(input_tokens=43000, output_tokens=900, cost_usd=Decimal("0.48")),
            outcome=CallOutcome.FAILED,
            purpose="cli",
        )
    )
    fake = _RaisingRunner(MalformedOutput("schema-invalid emit"), content_hash="a" * 64)

    with pytest.raises(MalformedOutput):
        run_extract(
            url="https://blog.example.com/x",
            interactive=False,
            auto=True,
            runner=fake,
            ledger=ledger,
            stdin_is_tty=False,
            out_dir=tmp_path,
            run_store=RunStore(runs),
        )

    record = _read_record(_only_run_dir(runs))
    assert record.status is RunStatus.FAILED
    assert record.lineage.model == "claude-opus-4-8"  # from the ledger (no spec emitted)
    assert record.lineage.input_hash == "a" * 64  # from the runner's content hash


def test_lineage_model_is_billed_ledger_model_not_spec_self_report(tmp_path: Path) -> None:
    """lineage.model is the billed provider model, never the LLM-authored
    extraction_metadata.model (architecture.md §1.5; investigation 0002 §7 — the real run
    self-reported "claude-sonnet" while the ledger billed claude-opus-4-8)."""
    from cyberlab_gen.cli.extract import (
        _persist_from_state,  # pyright: ignore[reportPrivateUsage]
        _populate_lineage,  # pyright: ignore[reportPrivateUsage]
    )
    from cyberlab_gen.errors import MalformedOutput
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.providers import (
        AgentLabel,
        CallOutcome,
        CapabilityHint,
        CostLedgerEntry,
        TokenUsage,
    )
    from cyberlab_gen.state.run_store import RunKind

    # The spec self-reports model "m" — the LLM-authored content field that used to win.
    state = PipelineState(blog_content="b", source_summary="s", spec=_in_scope_spec())
    assert state.spec is not None
    assert state.spec.extraction_metadata.model == "m"

    ledger = _ledger(None)
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime(2026, 6, 8, tzinfo=UTC),
            agent_label=AgentLabel.EXTRACTOR,
            provider="anthropic",
            model="claude-opus-4-8",  # what the framework actually billed
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            usage=TokenUsage(input_tokens=82365, output_tokens=31701, cost_usd=Decimal("1.2")),
            outcome=CallOutcome.SUCCESS,
            purpose="cli",
        )
    )

    handle = RunStore(tmp_path).start(kind=RunKind.EXTRACT, label="u")
    runner_obj = _RaisingRunner(MalformedOutput("unused"), content_hash="b" * 64)

    _persist_from_state(handle, state, ledger)  # must NOT write model="m"
    _populate_lineage(handle, runner=runner_obj, ledger=ledger)

    assert handle.record.lineage.model == "claude-opus-4-8"  # billed wins over the spec's "m"
    assert handle.record.lineage.extractor_version == "1.0.0"  # version still from the spec
    assert handle.record.lineage.input_hash == "b" * 64


# --- provenance family: the framework stamps the billed model (ADR 0065) ----
#
# Three instances of LLM-authored model-provenance (architecture.md §1.5 violation):
# extraction_metadata.model (a), proposed_by_model (b), lineage.model (c, already fixed).
# The single mechanism: the framework reads the billed model from the cost ledger and the
# LLM's self-report never wins.


def _billed_entry(model: str) -> object:
    from cyberlab_gen.providers import AgentLabel, CallOutcome, CapabilityHint, CostLedgerEntry
    from cyberlab_gen.providers.base import TokenUsage

    return CostLedgerEntry(
        timestamp=datetime(2026, 6, 8, tzinfo=UTC),
        agent_label=AgentLabel.EXTRACTOR,
        provider="anthropic",
        model=model,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        usage=TokenUsage(input_tokens=100, output_tokens=100, cost_usd=Decimal("1.2")),
        outcome=CallOutcome.SUCCESS,
        purpose="cli",
    )


def test_extraction_metadata_model_is_stamped_from_the_billed_ledger(tmp_path: Path) -> None:
    """(a) The framework stamps extraction_metadata.model from the billed ledger; the spec's
    LLM-authored self-report ("m") never wins (architecture.md §1.5; investigation 0002 §7)."""
    from cyberlab_gen.cli.extract import _stamp_billed_model  # pyright: ignore[reportPrivateUsage]
    from cyberlab_gen.providers.cost_ledger import CostLedgerEntry

    del tmp_path
    spec = _in_scope_spec()
    assert spec.extraction_metadata.model == "m"  # the LLM self-report
    ledger = _ledger(None)
    entry = _billed_entry("claude-opus-4-8")
    assert isinstance(entry, CostLedgerEntry)
    ledger.record(entry)

    stamped = _stamp_billed_model(spec, ledger)
    assert stamped.extraction_metadata.model == "claude-opus-4-8"  # billed wins
    # everything else on the spec is unchanged (model_copy is surgical)
    assert (
        stamped.extraction_metadata.extractor_version == spec.extraction_metadata.extractor_version
    )
    assert stamped.extraction_outcome is spec.extraction_outcome


def test_stamp_billed_model_is_a_noop_on_an_empty_ledger() -> None:
    """With nothing billed yet, the stamp is a no-op (keeps the spec's value) — the ledger
    is non-empty in practice, so the spec value is only ever a last-resort fallback."""
    from cyberlab_gen.cli.extract import _stamp_billed_model  # pyright: ignore[reportPrivateUsage]

    spec = _in_scope_spec()
    assert _stamp_billed_model(spec, _ledger(None)).extraction_metadata.model == "m"


def test_proposed_by_model_is_billed_ledger_model_not_spec_self_report(tmp_path: Path) -> None:
    """(b) proposed_by_model in the proposal audit block is the billed model, not the spec's
    self-report (schema.md:793 — framework-recorded; investigation 0002 §7)."""
    from cyberlab_gen.cli.extract import _acceptance_context  # pyright: ignore[reportPrivateUsage]
    from cyberlab_gen.providers.cost_ledger import CostLedgerEntry

    rr = RunResult(spec=_in_scope_spec())
    assert rr.spec.extraction_metadata.model == "m"
    ledger = _ledger(None)
    entry = _billed_entry("claude-opus-4-8")
    assert isinstance(entry, CostLedgerEntry)
    ledger.record(entry)

    ctx = _acceptance_context(rr, overlay_dir=tmp_path, handle=None, ledger=ledger)
    assert ctx.proposed_by_model == "claude-opus-4-8"  # billed, not "m"


# --- L4/G1: persistence recovers the partial spec from the checkpoint on abort ----
#
# The fakes above carry an explicit ``last_state`` and so mask the real gap; these
# drive the *real* ``PipelineExtractRunner`` (ingestion stubbed, no provider) whose
# pipeline aborts mid-graph. The in-memory "final" is never set on an abort, so the
# partial spec must be recovered straight from ``checkpoint.sqlite`` (ADR 0053).


def _abort_runner(monkeypatch: pytest.MonkeyPatch, *, facets: list[str]) -> PipelineExtractRunner:
    """A real ``PipelineExtractRunner`` whose pipeline aborts mid-graph (jury raises).

    Ingestion is stubbed so the run reaches the graph with no provider/network. The
    Extractor + Validator complete (checkpointing the partial spec); the jury then
    raises, so the graph never returns cleanly.
    """
    from cyberlab_gen.agents.extractor_jury.schema import Verdict
    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from tests.unit.framework.pipeline_fakes import (
        CrashOnceJury,
        FakeExtractor,
        install_stub_ingestion,
        make_ingestion,
        make_spec,
        make_validator,
        make_verdict,
    )

    install_stub_ingestion(monkeypatch, ingestion=make_ingestion(), blog_content="blog content")
    return PipelineExtractRunner(
        extractor=FakeExtractor([make_spec(facets=facets)]),  # type: ignore[arg-type]
        validator=make_validator(),
        jury=CrashOnceJury([make_verdict(Verdict.APPROVE)]),  # type: ignore[arg-type]
    )


def test_pipeline_runner_last_state_reads_checkpoint_on_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PipelineExtractRunner.last_state`` recovers the partial spec from the checkpoint."""
    runner = _abort_runner(monkeypatch, facets=["target:aws"])
    runner.enable_checkpointing(tmp_path / "checkpoint.sqlite", thread_id="run-A")

    with pytest.raises(RuntimeError, match="boom"):
        runner.run("https://example.com/x", ledger=_ledger(None))

    # No clean graph return happened, so the in-memory final was never set — last_state
    # is reconstructed from checkpoint.sqlite (the L4 fix).
    state = runner.last_state
    assert state is not None
    assert state.spec is not None
    assert list(state.spec.facets) == ["target:aws"]


def test_run_extract_persists_partial_spec_from_checkpoint_on_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-graph crash persists the checkpointed partial spec into the run dir."""
    runs = tmp_path / "runs"
    runner = _abort_runner(monkeypatch, facets=["target:aws"])

    with pytest.raises(RuntimeError, match="boom"):
        run_extract(
            url="https://example.com/x",
            interactive=False,
            auto=True,
            runner=runner,
            ledger=_ledger(None),
            stdin_is_tty=False,
            out_dir=tmp_path / "cwd",
            run_store=RunStore(runs),
        )

    run_dir = _only_run_dir(runs)
    assert (run_dir / SPEC_FILENAME).is_file()  # the partial spec was recovered + written
    record = _read_record(run_dir)
    assert SPEC_FILENAME in record.artifacts  # run.json lists it (not just cost.yaml)
    assert record.status is RunStatus.CRASHED


def test_sigterm_guard_converts_to_keyboard_interrupt() -> None:
    """The verb's SIGTERM guard makes a terminate signal raise ``KeyboardInterrupt``.

    That conversion is what lets a SIGTERM unwind through the run-store ``finally``
    (a partial run is saved) instead of killing the process without persisting.
    """
    import signal

    from cyberlab_gen.runtime import persisting_signal_guard

    before = signal.getsignal(signal.SIGTERM)
    with persisting_signal_guard():
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    assert signal.getsignal(signal.SIGTERM) == before  # restored on exit
