"""Integration tests for the ``plan`` verb + the post-Planner interrupt (Phase 2 Tasks 6 + 8).

Drive the verb wiring end-to-end through a **fake** ``PlanRunner`` (no live provider): the verb loads
the committed codebuild ``attack-spec.yaml`` fixture, runs, surfaces the post-Planner interrupt
(``--interactive``, the default), writes ``lab.yaml``, promotes accepted Planner facet proposals to a
**run-scoped** overlay (ADR 0100), and persists a run on every exit path. The fake produces a
hand-built valid ``LabManifest`` — this tests the *wiring*; the single real (paid) ``plan`` run on a
real Planner output is the maintainer's (eval-is-user-run).

The seam is **synchronous** at the verb boundary (``PlanRunner.run`` is called directly), so the fake
is a plain sync class — not the async ``pipeline_fakes.FakePlanner`` (which is the runner's internals).
Menu choices arrive via ``CliRunner(input=...)``; the ``$EDITOR`` and editor-revalidation paths are
driven against the engine helpers directly with an injected ``editor`` callable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from ruamel.yaml.compat import StringIO
from typer.testing import CliRunner

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.agents.proposals import ProposedFacet
from cyberlab_gen.agents.results import PlannerRefusal
from cyberlab_gen.cli import main as cli_main
from cyberlab_gen.cli.main import app
from cyberlab_gen.cli.plan import (
    LAB_MANIFEST_FILENAME,
    PLANNER_REFUSAL_FILENAME,
    PlanRunResult,
    run_plan,
)
from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus
from cyberlab_gen.providers import AgentLabel, CapabilityHint, TokenUsage
from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedger, CostLedgerEntry
from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.schemas.registries import FacetEntry
from cyberlab_gen.state.run_store import (
    COST_FILENAME,
    MANIFEST_FILENAME,
    RunKind,
    RunRecord,
    RunStatus,
    RunStore,
)
from tests.unit.framework.pipeline_fakes import make_manifest, make_verdict

runner = CliRunner()

_FIXTURE = Path(__file__).parent / "fixtures" / "codebuild-attack-spec.yaml"
_OVERLAY_DIRNAME = "registry-overlay"


class _FakePlanRunner:
    """A scripted sync ``PlanRunner``: a cursor over queued ``PlanRunResult``s (last repeats).

    ``run`` advances the cursor; ``re_run_with_feedback`` (the four-option Feedback path) advances it
    too and records the feedback. So a 2-result fake yields result[0] on the first plan and result[1]
    on the first feedback re-run — enough to drive Feedback → Approve.
    """

    def __init__(self, results: list[PlanRunResult]) -> None:
        self._results = results
        self.run_calls = 0
        self.feedback_calls: list[str] = []

    def _next(self) -> PlanRunResult:
        idx = min(self.run_calls + len(self.feedback_calls), len(self._results) - 1)
        return self._results[idx]

    def run(self, attack_spec: object, *, ledger: CostLedger) -> PlanRunResult:
        del attack_spec, ledger
        result = self._next()
        self.run_calls += 1
        return result

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> PlanRunResult:
        del ledger
        result = self._next()
        self.feedback_calls.append(feedback)
        return result


def _install_plan_runner(monkeypatch: pytest.MonkeyPatch, runner_obj: _FakePlanRunner) -> None:
    def _factory(_state: object) -> _FakePlanRunner:
        return runner_obj

    monkeypatch.setattr(cli_main, "plan_runner_factory", _factory, raising=True)


@pytest.fixture(autouse=True)
def _reset() -> None:  # pyright: ignore[reportUnusedFunction]
    cli_main.last_invocation_context = None
    cli_main.extract_runner_factory = None
    cli_main.plan_runner_factory = None
    cli_main.stdin_tty_override = None


def _ledger(model: str) -> CostLedger:
    ledger = CostLedger(run_id="t", cap_usd=None)
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime.now(UTC),
            agent_label=AgentLabel.PLANNER,
            provider="anthropic",
            model=model,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost_usd=Decimal("0.05")),
            outcome=CallOutcome.SUCCESS,
            purpose="cli",
        )
    )
    return ledger


def _planner_facet() -> ProposedFacet:
    """A Planner-authored facet proposal (``runtime:*`` — within the Planner's authority)."""
    return ProposedFacet(
        name="runtime:codebuild_project",
        category="runtime",
        description="a CodeBuild project provisioned by the lab at runtime",
        applies_at_levels=["lab"],
        reasoning="the lab provisions a CodeBuild project at runtime",
    )


def _planned(
    manifest: LabManifest | None = None, *, facet_proposals: list[ProposedFacet] | None = None
) -> PlanRunResult:
    return PlanRunResult(
        status=PlanPipelineStatus.PLANNED,
        manifest=manifest or make_manifest(),
        verdict=make_verdict(Verdict.APPROVE),
        facet_proposals=facet_proposals or [],
    )


def _only_run_dir(root: Path) -> Path:
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1, f"expected one run dir, found {dirs}"
    return dirs[0]


def _read_record(run_dir: Path) -> RunRecord:
    return RunRecord.model_validate_json((run_dir / "run.json").read_text(encoding="utf-8"))


# --- the slice: plan the codebuild AttackSpec, write a schema-valid lab.yaml (--auto) ---


def test_plan_auto_writes_valid_lab_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"

    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE), "--auto"])

    assert result.exit_code == 0, result.output
    written = tmp_path / LAB_MANIFEST_FILENAME
    assert written.exists()
    # the written lab.yaml round-trips as a schema-valid LabManifest (static-schema-valid by construction)
    manifest = LabManifest.from_yaml(written.read_text(encoding="utf-8"))
    assert manifest.spec_kind.value == "LabManifest"


def test_plan_rejects_a_lab_manifest_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `plan` consumes an AttackSpec; handed a LabManifest it is a clean usage error, not a crash.
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    from cyberlab_gen.cli.plan import manifest_to_yaml

    manifest_path = tmp_path / "lab.yaml"
    manifest_path.write_text(manifest_to_yaml(make_manifest()), encoding="utf-8")
    state_dir = tmp_path / "state"

    result = runner.invoke(
        app, ["--state-dir", str(state_dir), "plan", str(manifest_path), "--auto"]
    )

    assert result.exit_code == 1
    assert "AttackSpec" in result.output


def test_plan_rejects_malformed_yaml_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A syntactically-malformed spec file is a clean usage error (a wrapped CyberlabGenError), never an
    # uncaught ruamel traceback — the load gate's clean-exit contract holds for the parse step too.
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    bad = tmp_path / "broken.yaml"
    bad.write_text("spec_kind: AttackSpec\nfacets: [unclosed\n  : : :\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(bad), "--auto"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no raw traceback
    assert "not a valid spec file" in result.output


# --- the post-Planner interrupt: the four-option menu (--interactive, the default) ---


def test_plan_interactive_approve_writes_lab_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four-option menu: Approve writes the manifest (default mode, no flag)."""
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    # interactive needs a TTY; CliRunner provides stdin, so force the TTY check.
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="a\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / LAB_MANIFEST_FILENAME).exists()


def test_plan_interactive_abort_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four-option menu: Abort writes no file and exits non-zero."""
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="b\n")
    assert result.exit_code == 1
    assert not (tmp_path / LAB_MANIFEST_FILENAME).exists()
    assert "aborted" in result.output.lower()
    # a user abort at the interrupt persists the run as ABORTED, not SHIPPED (the manifest was
    # plan-ready but not accepted).
    assert _read_record(_only_run_dir(state_dir / "runs")).status is RunStatus.ABORTED


def test_plan_interactive_feedback_reruns_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Option 2 (natural-language feedback) re-runs the Planner, then Approve ships."""
    fake = _FakePlanRunner([_planned(), _planned()])
    _install_plan_runner(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # feedback choice -> feedback text -> approve
    result = runner.invoke(
        app,
        ["--state-dir", str(state_dir), "plan", str(_FIXTURE)],
        input="f\nregroup the phases\na\n",
    )
    assert result.exit_code == 0, result.output
    assert fake.feedback_calls == ["regroup the phases"]
    assert (tmp_path / LAB_MANIFEST_FILENAME).exists()


def test_plan_feedback_then_route_back_persists_the_reruns_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Feedback re-run that routes back must persist the RE-RUN's state, not the stale first run.

    The plan orchestrator *returns* its terminal states (ADR 0097), so the final driver result must
    travel back to ``run_plan``'s persistence (adversarial-review finding, ADR 0100). Here the first
    run is plannable and the post-feedback re-run routes back: the record must be FAILED with the
    re-run's refusal persisted — not the stale ABORTED + missing refusal of the pre-fix bug.
    """
    refusal = PlannerRefusal(
        summary="step 2 assumes credentials step 1 never establishes",  # type: ignore[arg-type]
        attack_spec_field_paths=["chain.chain_steps[1].description"],  # type: ignore[arg-type]
        detail="incoherent; the Planner may not repair it",  # type: ignore[arg-type]
    )
    fake = _FakePlanRunner(
        [
            _planned(),  # first run: a plannable manifest
            PlanRunResult(status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR, refusal=refusal),
        ]
    )
    _install_plan_runner(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # feedback -> text; the re-run routes back, so the loop reports it and exits (no further menu).
    result = runner.invoke(
        app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="f\nre-extract please\n"
    )
    assert result.exit_code == 1
    assert not (tmp_path / LAB_MANIFEST_FILENAME).exists()
    run_dir = _only_run_dir(state_dir / "runs")
    record = _read_record(run_dir)
    assert record.status is RunStatus.FAILED  # the re-run's route-back, not stale ABORTED
    assert (run_dir / PLANNER_REFUSAL_FILENAME).is_file()  # the re-run's refusal IS persisted


def test_plan_feedback_then_low_confidence_persists_low_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Feedback re-run that ships low-confidence must persist SHIPPED_LOW_CONFIDENCE, not stale SHIPPED."""
    fake = _FakePlanRunner(
        [
            _planned(),  # first run: a clean PLANNED
            PlanRunResult(
                status=PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
                manifest=make_manifest(),
                verdict=make_verdict(Verdict.APPROVE),
                low_jury_confidence=True,
                unresolved_feedback=["phase grouping unclear"],
            ),
        ]
    )
    _install_plan_runner(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # feedback -> text -> approve the low-confidence re-run
    result = runner.invoke(
        app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="f\nregroup\na\n"
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / LAB_MANIFEST_FILENAME).exists()
    record = _read_record(_only_run_dir(state_dir / "runs"))
    assert (
        record.status is RunStatus.SHIPPED_LOW_CONFIDENCE
    )  # the re-run's state, not stale SHIPPED


# --- per-facet-proposal Accept/Edit + run-scoped promotion (ADR 0100) ---


def test_plan_interactive_facet_proposal_accept_promotes_to_run_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After Approve, the per-proposal Accept menu fires and promotes to the RUN-scoped overlay."""
    _install_plan_runner(
        monkeypatch, _FakePlanRunner([_planned(facet_proposals=[_planner_facet()])])
    )
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    # approve manifest -> accept the facet proposal
    result = runner.invoke(
        app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="a\na\n"
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / LAB_MANIFEST_FILENAME).exists()
    assert "runtime:codebuild_project" in result.output
    # Accept wrote the facet to the RUN-scoped overlay, marked human-approved + planner-stamped (ADR
    # 0100). NOT to the shared per-state overlay.
    assert not (state_dir / _OVERLAY_DIRNAME).exists()
    run_overlay = _only_run_dir(state_dir / "runs") / _OVERLAY_DIRNAME
    facets = load_overlay_file(run_overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in facets.entries] == ["runtime:codebuild_project"]
    assert facets.entries[0].proposed_by == "planner"
    audit = facets.proposals["runtime:codebuild_project"]
    assert audit.approval == "human"
    assert audit.proposal_origin == "llm_during_planning"
    assert audit.source_lab == "codebuild-lab"  # a Planner-stage proposal fills in the lab id


def test_plan_interactive_abort_promotes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion is gated on ship (ADR 0050/0062): aborting after seeing a proposal writes no overlay."""
    _install_plan_runner(
        monkeypatch, _FakePlanRunner([_planned(facet_proposals=[_planner_facet()])])
    )
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli_main, "stdin_tty_override", True)
    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)], input="b\n")
    assert result.exit_code == 1
    run_dir = _only_run_dir(state_dir / "runs")
    assert not (run_dir / _OVERLAY_DIRNAME).exists()  # no orphan promotion on abort


def test_plan_auto_promotes_facet_to_run_overlay_not_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--auto`` skips the interrupt and auto-promotes facets to the RUN overlay, never the shared one.

    This is the ADR-0100 safety property: a dev/eval ``plan`` run (eval is ``--auto``) must not mutate
    the shared production vocabulary at ``<state>/registry-overlay``.
    """
    _install_plan_runner(
        monkeypatch, _FakePlanRunner([_planned(facet_proposals=[_planner_facet()])])
    )
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE), "--auto"])
    assert result.exit_code == 0, result.output
    # the shared per-state overlay was NOT written (run-scoping)
    assert not (state_dir / _OVERLAY_DIRNAME).exists()
    run_overlay = _only_run_dir(state_dir / "runs") / _OVERLAY_DIRNAME
    facets = load_overlay_file(run_overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in facets.entries] == ["runtime:codebuild_project"]
    assert facets.proposals["runtime:codebuild_project"].approval == "auto"


def test_plan_auto_route_back_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--auto`` on a route-back surfaces the actionable message and exits non-zero (no lab.yaml)."""
    refusal = PlannerRefusal(
        summary="step 2 assumes credentials step 1 never establishes",  # type: ignore[arg-type]
        attack_spec_field_paths=["chain.chain_steps[1].description"],  # type: ignore[arg-type]
        detail="the chain is incoherent and the Planner may not repair it",  # type: ignore[arg-type]
    )
    _install_plan_runner(
        monkeypatch,
        _FakePlanRunner(
            [PlanRunResult(status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR, refusal=refusal)]
        ),
    )
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"
    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE), "--auto"])
    assert result.exit_code == 1
    assert not (tmp_path / LAB_MANIFEST_FILENAME).exists()
    assert "incoherent" in result.output.lower()


# --- mode-flag guards (headless, mutually-exclusive) ---


def test_plan_headless_interactive_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--interactive`` is rejected when stdin is not a TTY, pointing to ``--auto``."""
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "stdin_tty_override", False)
    result = runner.invoke(app, ["plan", str(_FIXTURE), "--interactive"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "--auto" in combined
    assert not (tmp_path / LAB_MANIFEST_FILENAME).exists()


def test_plan_rejects_both_interactive_and_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    result = runner.invoke(app, ["plan", str(_FIXTURE), "--interactive", "--auto"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "mutually exclusive" in combined


# --- engine-level: editor revalidation (manifest + facet proposal) ---


def test_manifest_edit_revalidates_and_reopens_on_invalid() -> None:
    """An artifact Edit with a structurally-invalid edit reopens with error comments (``§3.1.1``)."""
    from cyberlab_gen.cli.plan import (
        _edit_manifest_with_revalidation,  # pyright: ignore[reportPrivateUsage]
        manifest_to_yaml,
    )

    manifest = make_manifest()
    # the first edit breaks spec_version (int); the second renames the lab and is valid.
    broken = manifest_to_yaml(manifest).replace("spec_version: 1", "spec_version: notanint")
    valid_edit = manifest_to_yaml(manifest).replace("name: CodeBuild Lab", "name: Edited Lab")
    edits = iter([broken, valid_edit])
    seen: list[str] = []

    def editor(text: str) -> str:
        seen.append(text)
        return next(edits)

    edited = _edit_manifest_with_revalidation(manifest, editor=editor)
    assert edited.core.name == "Edited Lab"  # the second (valid) edit was accepted...
    # ...and the editor reopened with the structural-failure comment after the first invalid edit.
    assert any("STRUCTURAL VALIDATION FAILED" in t for t in seen[1:])


def test_manifest_edit_unchanged_keeps_original() -> None:
    """An editor that returns the text unchanged keeps the original manifest."""
    from cyberlab_gen.cli.plan import (
        _edit_manifest_with_revalidation,  # pyright: ignore[reportPrivateUsage]
    )

    manifest = make_manifest()
    assert _edit_manifest_with_revalidation(manifest, editor=lambda _t: None) == manifest


def test_facet_proposal_edit_revalidates_and_reopens_on_invalid() -> None:
    """A per-proposal Edit revalidates structurally; an invalid edit reopens with comments."""
    from cyberlab_gen.cli import interrupt
    from cyberlab_gen.cli.plan import _proposal_to_yaml  # pyright: ignore[reportPrivateUsage]

    broken = "name: x\n"  # missing required category / description / applies_at_levels / reasoning
    valid = (
        "name: runtime:edited\ncategory: runtime\ndescription: a thing\n"
        "applies_at_levels:\n- lab\nreasoning: still needed\n"
    )
    edits = iter([broken, valid])
    seen: list[str] = []

    def editor(text: str) -> str:
        seen.append(text)
        return next(edits)

    edited = interrupt.review_one_proposal(
        label="facet",
        model=_planner_facet(),
        to_text=_proposal_to_yaml,
        parse=lambda text: ProposedFacet.model_validate(interrupt.yaml().load(StringIO(text))),
        editor=editor,
        choice_reader=lambda: "e",  # force Edit
    )
    assert edited.name == "runtime:edited"
    assert any("STRUCTURAL VALIDATION FAILED" in t for t in seen[1:])


# --- persistence: billed Planner model on the manifest + lineage (run_plan direct, --auto) ---


def test_plan_persists_run_with_billed_planner_model(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    written = run_plan(
        spec_path=_FIXTURE,
        runner=_FakePlanRunner([_planned()]),
        ledger=_ledger("opus-billed-planner"),
        auto=True,
        out_dir=out_dir,
        run_store=RunStore(runs),
    )

    assert written is not None
    assert written == out_dir / LAB_MANIFEST_FILENAME
    assert written.exists()
    # the cwd deliverable carries the billed Planner model (stamped at the ship boundary)
    assert (
        LabManifest.from_yaml(written.read_text(encoding="utf-8")).core.generation.model
        == "opus-billed-planner"
    )
    run_dir = _only_run_dir(runs)
    assert (run_dir / MANIFEST_FILENAME).is_file()  # the run-dir mirror
    assert (run_dir / COST_FILENAME).is_file()
    record = _read_record(run_dir)
    assert record.kind is RunKind.PLAN
    assert record.status is RunStatus.SHIPPED
    assert record.lineage.model == "opus-billed-planner"  # billed, never the LLM self-report


def test_plan_route_back_exits_nonzero_and_persists_refusal(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    refusal = PlannerRefusal(
        summary="step 2 assumes credentials step 1 never establishes",  # type: ignore[arg-type]
        attack_spec_field_paths=["chain.chain_steps[1].description"],  # type: ignore[arg-type]
        detail="the chain is incoherent and the Planner may not repair it",  # type: ignore[arg-type]
    )
    written = run_plan(
        spec_path=_FIXTURE,
        runner=_FakePlanRunner(
            [PlanRunResult(status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR, refusal=refusal)]
        ),
        ledger=_ledger("opus-billed-planner"),
        auto=True,
        out_dir=out_dir,
        run_store=RunStore(runs),
    )

    assert written is None  # did not ship → the verb exits non-zero
    assert not (out_dir / LAB_MANIFEST_FILENAME).exists()  # no lab.yaml on a route-back
    run_dir = _only_run_dir(runs)
    assert (run_dir / PLANNER_REFUSAL_FILENAME).is_file()  # the structured refusal is persisted
    assert _read_record(run_dir).lineage.model == "opus-billed-planner"  # billed on every exit path


def test_plan_cross_check_unresolved_halts_without_shipping(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    written = run_plan(
        spec_path=_FIXTURE,
        runner=_FakePlanRunner(
            [
                PlanRunResult(
                    status=PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED,
                    manifest=make_manifest(),  # a known-broken manifest, for inspection
                    halt_reason="dangling identifier_source",
                    unresolved_feedback=["phases[0]...: unresolved"],
                )
            ]
        ),
        ledger=_ledger("opus-billed-planner"),
        auto=True,
        out_dir=out_dir,
        run_store=RunStore(runs),
    )

    assert written is None  # known-broken → never shipped to cwd
    assert not (out_dir / LAB_MANIFEST_FILENAME).exists()
    run_dir = _only_run_dir(runs)
    # the failed manifest IS persisted to the run dir for inspection (not shipped to cwd)
    assert (run_dir / MANIFEST_FILENAME).is_file()
    assert _read_record(run_dir).status is RunStatus.HALTED_VALIDATION


def test_plan_auto_promotes_to_explicit_overlay_seam(tmp_path: Path) -> None:
    """The ``overlay_dir`` seam (test/dev override) targets a named overlay instead of the run dir."""
    runs = tmp_path / "runs"
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    overlay = tmp_path / "explicit-overlay"
    written = run_plan(
        spec_path=_FIXTURE,
        runner=_FakePlanRunner([_planned(facet_proposals=[_planner_facet()])]),
        ledger=_ledger("opus-billed-planner"),
        auto=True,
        out_dir=out_dir,
        run_store=RunStore(runs),
        overlay_dir=overlay,
    )
    assert written is not None
    facets = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in facets.entries] == ["runtime:codebuild_project"]
    assert facets.proposals["runtime:codebuild_project"].approval == "auto"
    # the run dir's own overlay was NOT used when an explicit one is given
    assert not (_only_run_dir(runs) / _OVERLAY_DIRNAME).exists()
