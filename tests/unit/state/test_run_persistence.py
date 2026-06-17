"""Unit tests for the shared run persistence/lineage service (ADR 0068).

The single home of the billed-model invariant (``architecture.md §1.5``; ADR 0065): whatever spec
gets persisted records the model the framework *billed* (from the cost ledger), never the LLM's
self-reported ``extraction_metadata.model``. Both the ``extract`` CLI and the eval harness consume
this service, so the invariant cannot drift between them again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.providers import AgentLabel, CapabilityHint, TokenUsage
from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedger, CostLedgerEntry
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.manifest import CURRENT_MANIFEST_VERSION, LabManifest
from cyberlab_gen.state.run_persistence import (
    billed_model,
    persist_pipeline_artifacts,
    persist_plan_artifacts,
    stamp_billed_model,
    stamp_framework_provenance,
    stamp_spec_version,
)
from cyberlab_gen.state.run_store import (
    JURY_VERDICT_FILENAME,
    MANIFEST_FILENAME,
    SPEC_FILENAME,
    RunKind,
    RunLineage,
    RunStore,
)
from tests.unit.framework.pipeline_fakes import make_manifest, make_spec, make_verdict

if TYPE_CHECKING:
    from pathlib import Path


def _ledger_billing(model: str, *, label: AgentLabel = AgentLabel.EXTRACTOR) -> CostLedger:
    ledger = CostLedger(run_id="t", cap_usd=None)
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime.now(UTC),
            agent_label=label,
            provider="anthropic",
            model=model,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost_usd=Decimal("0.01")),
            outcome=CallOutcome.SUCCESS,
            purpose="extract",
        )
    )
    return ledger


def _spec_self_reporting(model: str) -> AttackSpec:
    spec = make_spec()
    return spec.model_copy(
        update={"extraction_metadata": spec.extraction_metadata.model_copy(update={"model": model})}
    )


def test_billed_model_reads_ledger_and_handles_empty() -> None:
    assert billed_model(_ledger_billing("opus-billed")) == "opus-billed"
    assert billed_model(CostLedger(run_id="t", cap_usd=None)) is None


def test_stamp_billed_model_overrides_self_report_and_is_idempotent() -> None:
    ledger = _ledger_billing("opus-billed")
    stamped = stamp_billed_model(_spec_self_reporting("sonnet-selfreport"), ledger)
    assert stamped.extraction_metadata.model == "opus-billed"
    # re-stamping with the same ledger is a no-op
    assert stamp_billed_model(stamped, ledger).extraction_metadata.model == "opus-billed"


def test_stamp_is_noop_on_empty_ledger() -> None:
    """No billed entry yet → keep the spec's own value as a last-resort fallback."""
    spec = _spec_self_reporting("sonnet-selfreport")
    out = stamp_billed_model(spec, CostLedger(run_id="t", cap_usd=None))
    assert out.extraction_metadata.model == "sonnet-selfreport"


def test_persist_records_billed_model_for_partial_spec(tmp_path: Path) -> None:
    """On the partial (``state.spec``) path the persisted spec AND lineage record the billed
    model, never the self-report — the exact leak the eval sibling had on halt (ADR 0068).
    """
    from cyberlab_gen.framework.orchestrator import PipelineState

    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.EXTRACT, label="x", lineage=RunLineage(input_ref="x"))
    state = PipelineState(
        blog_content="b", source_summary="s", spec=_spec_self_reporting("sonnet-selfreport")
    )
    persist_pipeline_artifacts(
        handle,
        state=state,
        shipped_spec=None,
        ledger=_ledger_billing("opus-billed"),
        content_hash="a" * 64,
    )

    assert handle.record.lineage.model == "opus-billed"
    persisted = AttackSpec.from_yaml((handle.directory / SPEC_FILENAME).read_text(encoding="utf-8"))
    assert persisted.extraction_metadata.model == "opus-billed"


def test_framework_provenance_does_not_author_completeness_score() -> None:
    """The framework stamps model + spec_version but must NEVER author ``completeness_score`` — it
    is the LLM's self-report, not a framework fact (ADR 0070). Regression guard: if a future change
    folds completeness_score into the provenance stamp, this fails.
    """
    spec = make_spec()  # completeness_score == 0.8
    stamped = stamp_framework_provenance(spec, _ledger_billing("opus-billed"))
    assert (
        stamped.extraction_metadata.completeness_score
        == spec.extraction_metadata.completeness_score
    )


def test_stamp_spec_version_sets_current() -> None:
    """The framework stamps ``spec_version`` to ``CURRENT_ATTACK_SPEC_VERSION``; the model never owns it."""
    from cyberlab_gen.schemas.attack_spec import CURRENT_ATTACK_SPEC_VERSION

    spec = make_spec().model_copy(update={"spec_version": 99})
    assert stamp_spec_version(spec).spec_version == CURRENT_ATTACK_SPEC_VERSION


def test_persist_stamps_current_spec_version(tmp_path: Path) -> None:
    """A persisted spec always carries ``CURRENT_ATTACK_SPEC_VERSION`` — the framework owns it (ADR 0069),
    so the LLM-emitted value (here a stray 99) never reaches disk.
    """
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.schemas.attack_spec import CURRENT_ATTACK_SPEC_VERSION

    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.EXTRACT, label="x", lineage=RunLineage(input_ref="x"))
    state = PipelineState(
        blog_content="b",
        source_summary="s",
        spec=make_spec().model_copy(update={"spec_version": 99}),
    )
    persist_pipeline_artifacts(
        handle,
        state=state,
        shipped_spec=None,
        ledger=_ledger_billing("opus-billed"),
        content_hash="a" * 64,
    )
    persisted = AttackSpec.from_yaml((handle.directory / SPEC_FILENAME).read_text(encoding="utf-8"))
    assert persisted.spec_version == CURRENT_ATTACK_SPEC_VERSION


def test_persist_prefers_shipped_spec_and_still_stamps(tmp_path: Path) -> None:
    """A supplied ``shipped_spec`` is the one persisted, still billed-stamped."""
    from cyberlab_gen.framework.orchestrator import PipelineState

    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.EXTRACT, label="x", lineage=RunLineage(input_ref="x"))
    state = PipelineState(
        blog_content="b", source_summary="s", spec=_spec_self_reporting("sonnet-partial")
    )
    persist_pipeline_artifacts(
        handle,
        state=state,
        shipped_spec=_spec_self_reporting("sonnet-shipped"),
        ledger=_ledger_billing("opus-billed"),
        content_hash="a" * 64,
    )
    persisted = AttackSpec.from_yaml((handle.directory / SPEC_FILENAME).read_text(encoding="utf-8"))
    assert persisted.extraction_metadata.model == "opus-billed"


# --- the manifest (plan) side: one generalized stamp home, one billed-model invariant -------


def test_stamp_framework_provenance_stamps_manifest_generation() -> None:
    """The one generalized stamp home dispatches on artifact type: a ``LabManifest`` gets its
    GenerationBlock stamped — billed Planner model (not the LLM placeholder), the package
    ``tool_version``, a fresh ``timestamp`` — and ``spec_version`` set to current (ADR 0086/0069).
    """
    from importlib.metadata import version

    manifest = make_manifest().model_copy(update={"spec_version": 99})
    ledger = _ledger_billing("opus-billed-planner", label=AgentLabel.PLANNER)

    stamped = stamp_framework_provenance(manifest, ledger)

    assert isinstance(stamped, LabManifest)
    assert (
        stamped.core.generation.model == "opus-billed-planner"
    )  # billed, never the LLM self-report
    assert stamped.core.generation.tool_version == version("cyberlab-gen")
    # timestamp is framework-stamped fresh (within the last minute), not the LLM's value
    assert datetime.now(UTC) - stamped.core.generation.timestamp < timedelta(minutes=1)
    assert stamped.spec_version == CURRENT_MANIFEST_VERSION  # the stray 99 never survives


def test_stamp_dispatches_per_artifact_billed_model_home() -> None:
    """Same call, different artifact: the billed model lands on ``extraction_metadata.model`` for an
    AttackSpec and on ``core.generation.model`` for a LabManifest — the dispatch is by type, and
    neither artifact's billed-model home is the other's.
    """
    spec = stamp_framework_provenance(
        _spec_self_reporting("self"), _ledger_billing("opus-extractor")
    )
    assert spec.extraction_metadata.model == "opus-extractor"

    manifest = stamp_framework_provenance(
        make_manifest(), _ledger_billing("opus-planner", label=AgentLabel.PLANNER)
    )
    assert manifest.core.generation.model == "opus-planner"


def test_stamp_manifest_is_noop_on_empty_ledger() -> None:
    """No billed entry → keep the manifest's existing GenerationBlock.model (last-resort fallback)."""
    manifest = make_manifest()
    prior_model = manifest.core.generation.model
    out = stamp_framework_provenance(manifest, CostLedger(run_id="t", cap_usd=None))
    assert isinstance(out, LabManifest)
    assert out.core.generation.model == prior_model


def test_persist_plan_artifacts_stamps_billed_planner_model(tmp_path: Path) -> None:
    """A plan run's persisted manifest AND lineage record the billed Planner model, and the jury
    verdict is written — the billed-model invariant, reused (not copied) from the extract path.
    """
    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.PLAN, label="codebuild", lineage=RunLineage(input_ref="cb"))
    persist_plan_artifacts(
        handle,
        manifest=make_manifest(),
        verdict=make_verdict(Verdict.APPROVE),
        ledger=_ledger_billing("opus-billed-planner", label=AgentLabel.PLANNER),
        content_hash="a" * 64,
    )

    assert handle.record.lineage.model == "opus-billed-planner"
    persisted = LabManifest.from_yaml(
        (handle.directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert persisted.core.generation.model == "opus-billed-planner"
    assert (handle.directory / JURY_VERDICT_FILENAME).is_file()


def test_persist_plan_records_billed_model_with_no_manifest(tmp_path: Path) -> None:
    """On a halt/route-back exit path (no manifest), ``lineage.model`` still records the billed
    Planner model — the record is honest about the billed model on every exit path (ADR 0068).
    """
    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.PLAN, label="cb", lineage=RunLineage(input_ref="cb"))
    persist_plan_artifacts(
        handle,
        manifest=None,
        verdict=None,
        ledger=_ledger_billing("opus-billed-planner", label=AgentLabel.PLANNER),
    )
    assert handle.record.lineage.model == "opus-billed-planner"
    assert MANIFEST_FILENAME not in handle.record.artifacts  # nothing to write


def test_persist_plan_does_not_overwrite_a_mirrored_manifest(tmp_path: Path) -> None:
    """A clean ship mirrors the stamped manifest at the ship boundary; persist (from ``finally``)
    must not re-write it (which would re-stamp a fresh, inconsistent ``timestamp``).
    """
    store = RunStore(tmp_path / "runs")
    handle = store.start(kind=RunKind.PLAN, label="cb", lineage=RunLineage(input_ref="cb"))
    # ship boundary already mirrored a manifest with a known timestamp
    ship_stamped = stamp_framework_provenance(
        make_manifest(), _ledger_billing("opus-billed-planner", label=AgentLabel.PLANNER)
    )
    handle.write_artifact(MANIFEST_FILENAME, ship_stamped)
    mirrored_ts = ship_stamped.core.generation.timestamp

    persist_plan_artifacts(
        handle,
        manifest=make_manifest(),  # a different (unstamped) object — must be ignored
        verdict=None,
        ledger=_ledger_billing("opus-billed-planner", label=AgentLabel.PLANNER),
    )
    persisted = LabManifest.from_yaml(
        (handle.directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert persisted.core.generation.timestamp == mirrored_ts  # the mirror was not overwritten
