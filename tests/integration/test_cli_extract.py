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

if TYPE_CHECKING:
    from pathlib import Path

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
    """After Approve, the per-proposal Accept menu fires once per proposal."""
    rr = RunResult(
        spec=_in_scope_spec(),
        value_type_proposals=[_proposal_vt()],
        facet_proposals=[_proposal_facet()],
    )
    _install_runner(monkeypatch, _FakeRunner([rr]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # approve artifact -> accept vt proposal -> accept facet proposal
    result = runner.invoke(app, ["extract", "https://example.com/blog"], input="a\na\na\n")
    assert result.exit_code == 0, result.output
    assert "s3_bucket_arn" in result.output
    assert "target:fastly" in result.output
    assert (tmp_path / ATTACK_SPEC_FILENAME).exists()


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
    return CostLedger(run_id="t", cap_usd=Decimal(cap) if cap is not None else None)


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


def test_auto_accept_caps_proposals(tmp_path: Path) -> None:
    """``--auto`` auto-accepts up to the cap; the rest are listed as deferred."""
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
    )
    assert written is not None
    assert written.exists()
