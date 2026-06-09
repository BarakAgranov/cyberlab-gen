"""Unit tests for the shared run persistence/lineage service (ADR 0068).

The single home of the billed-model invariant (``architecture.md §1.5``; ADR 0065): whatever spec
gets persisted records the model the framework *billed* (from the cost ledger), never the LLM's
self-reported ``extraction_metadata.model``. Both the ``extract`` CLI and the eval harness consume
this service, so the invariant cannot drift between them again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from cyberlab_gen.providers import AgentLabel, CapabilityHint, TokenUsage
from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedger, CostLedgerEntry
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.state.run_persistence import (
    billed_model,
    persist_pipeline_artifacts,
    stamp_billed_model,
    stamp_spec_version,
)
from cyberlab_gen.state.run_store import SPEC_FILENAME, RunKind, RunLineage, RunStore
from tests.unit.framework.pipeline_fakes import make_spec

if TYPE_CHECKING:
    from pathlib import Path


def _ledger_billing(model: str) -> CostLedger:
    ledger = CostLedger(run_id="t", cap_usd=None)
    ledger.record(
        CostLedgerEntry(
            timestamp=datetime.now(UTC),
            agent_label=AgentLabel.EXTRACTOR,
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


def test_stamp_spec_version_sets_current() -> None:
    """The framework stamps ``spec_version`` to ``CURRENT_SPEC_VERSION``; the model never owns it."""
    from cyberlab_gen.schemas.attack_spec import CURRENT_SPEC_VERSION

    spec = make_spec().model_copy(update={"spec_version": 99})
    assert stamp_spec_version(spec).spec_version == CURRENT_SPEC_VERSION


def test_persist_stamps_current_spec_version(tmp_path: Path) -> None:
    """A persisted spec always carries ``CURRENT_SPEC_VERSION`` — the framework owns it (ADR 0069),
    so the LLM-emitted value (here a stray 99) never reaches disk.
    """
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.schemas.attack_spec import CURRENT_SPEC_VERSION

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
    assert persisted.spec_version == CURRENT_SPEC_VERSION


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
