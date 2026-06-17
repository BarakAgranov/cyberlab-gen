"""Integration tests for the ``plan`` verb (Phase 2 Task 6).

Drive the verb wiring end-to-end through a **fake** ``PlanRunner`` (no live provider): the verb loads
the committed codebuild ``attack-spec.yaml`` fixture, runs, writes ``lab.yaml``, and persists a run on
every exit path. The fake produces a hand-built valid ``LabManifest`` — this tests the *wiring*; the
single real (paid) ``plan`` run on a real Planner output is the maintainer's (eval-is-user-run).

The seam is **synchronous** at the verb boundary (``PlanRunner.run`` is called directly), so the fake
is a plain sync class — not the async ``pipeline_fakes.FakePlanner`` (which is the runner's internals).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cyberlab_gen.agents.extractor_jury.schema import Verdict
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
from cyberlab_gen.schemas.manifest import LabManifest
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


class _FakePlanRunner:
    """A scripted sync ``PlanRunner``: returns queued ``PlanRunResult``s in order (last repeats)."""

    def __init__(self, results: list[PlanRunResult]) -> None:
        self._results = results
        self.run_calls = 0

    def run(self, attack_spec: object, *, ledger: CostLedger) -> PlanRunResult:
        del attack_spec, ledger
        idx = min(self.run_calls, len(self._results) - 1)
        self.run_calls += 1
        return self._results[idx]


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


def _planned(manifest: LabManifest | None = None) -> PlanRunResult:
    return PlanRunResult(
        status=PlanPipelineStatus.PLANNED,
        manifest=manifest or make_manifest(),
        verdict=make_verdict(Verdict.APPROVE),
    )


def _only_run_dir(root: Path) -> Path:
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1, f"expected one run dir, found {dirs}"
    return dirs[0]


def _read_record(run_dir: Path) -> RunRecord:
    return RunRecord.model_validate_json((run_dir / "run.json").read_text(encoding="utf-8"))


# --- the slice: plan the codebuild AttackSpec, write a schema-valid lab.yaml ---


def test_plan_writes_valid_lab_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "state"

    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(_FIXTURE)])

    assert result.exit_code == 0, result.output
    written = tmp_path / LAB_MANIFEST_FILENAME
    assert written.exists()
    # the written lab.yaml round-trips as a schema-valid LabManifest (Layer-1-valid by construction)
    manifest = LabManifest.from_yaml(written.read_text(encoding="utf-8"))
    assert manifest.spec_kind.value == "LabManifest"


def test_plan_rejects_a_lab_manifest_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `plan` consumes an AttackSpec; handed a LabManifest it is a clean usage error, not a crash.
    _install_plan_runner(monkeypatch, _FakePlanRunner([_planned()]))
    from cyberlab_gen.cli.plan import manifest_to_yaml

    manifest_path = tmp_path / "lab.yaml"
    manifest_path.write_text(manifest_to_yaml(make_manifest()), encoding="utf-8")
    state_dir = tmp_path / "state"

    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(manifest_path)])

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

    result = runner.invoke(app, ["--state-dir", str(state_dir), "plan", str(bad)])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no raw traceback
    assert "not a valid spec file" in result.output


# --- persistence: billed Planner model on the manifest + lineage ---------------


def test_plan_persists_run_with_billed_planner_model(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    out_dir = tmp_path / "cwd"
    out_dir.mkdir()
    written = run_plan(
        spec_path=_FIXTURE,
        runner=_FakePlanRunner([_planned()]),
        ledger=_ledger("opus-billed-planner"),
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
        out_dir=out_dir,
        run_store=RunStore(runs),
    )

    assert written is None  # known-broken → never shipped to cwd
    assert not (out_dir / LAB_MANIFEST_FILENAME).exists()
    run_dir = _only_run_dir(runs)
    # the failed manifest IS persisted to the run dir for inspection (not shipped to cwd)
    assert (run_dir / MANIFEST_FILENAME).is_file()
    assert _read_record(run_dir).status is RunStatus.HALTED_VALIDATION
