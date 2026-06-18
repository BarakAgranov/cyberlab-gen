"""Unit tests for the run store (ADR 0039).

The run store is the system's on-disk memory of everything a run produces. These
tests pin the guarantees the brief insists on:

- Starting a run creates a directory and a readable ``run.json`` *before* any work.
- Runs are identifiable and **never silently overwritten** (same label + same instant
  still yields distinct directories).
- Every artifact is persisted, complete or partial, and the record lists what is on
  disk.
- ``finalize`` is idempotent — the first terminal status wins (so a signal handler
  firing after a clean finish cannot corrupt the record).
- Writes are best-effort: an ``OSError`` is swallowed and logged, never raised, so
  persistence cannot mask the original error.
- Real vs. eval runs are separated by *location* (the store's ``root``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from cyberlab_gen.providers import (
    AgentLabel,
    CallOutcome,
    CapabilityHint,
    CostLedger,
    CostLedgerEntry,
    TokenUsage,
)
from cyberlab_gen.state.run_store import (
    BLOBS_DIRNAME,
    COST_FILENAME,
    RUN_RECORD_FILENAME,
    SPEC_FILENAME,
    TRAJECTORY_FILENAME,
    RunKind,
    RunLineage,
    RunRecord,
    RunStatus,
    RunStore,
)

if TYPE_CHECKING:
    import pytest

_NOW = datetime(2026, 6, 6, 10, 15, 0, tzinfo=UTC)


def _ledger_with_one_call(cost: str = "1.50") -> CostLedger:
    ledger = CostLedger(run_id="test", cap_usd=None)
    ledger.record(
        CostLedgerEntry(
            timestamp=_NOW,
            agent_label=AgentLabel.EXTRACTOR,
            provider="anthropic",
            model="claude-opus-4-8",
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            usage=TokenUsage(input_tokens=100, output_tokens=50, cost_usd=Decimal(cost)),
            outcome=CallOutcome.SUCCESS,
            purpose="extract",
        )
    )
    return ledger


def test_start_creates_dir_and_initial_record(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="https://blog.example.com/x", now=_NOW)

    assert handle.directory.is_dir()
    record_path = handle.directory / RUN_RECORD_FILENAME
    assert record_path.is_file()  # readable *before* any work happens

    record = RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    assert record.status is RunStatus.RUNNING
    assert record.kind is RunKind.EXTRACT
    assert record.label == "https://blog.example.com/x"
    assert record.ended_at is None
    assert record.artifacts == []


def test_run_id_includes_timestamp_and_url_slug(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(
        kind=RunKind.EXTRACT, label="https://blog.example.com/posts/aws-attack", now=_NOW
    )
    assert handle.directory.name.startswith("20260606T101500Z-")
    # host + last path segment, slugified
    assert "blog-example-com" in handle.directory.name
    assert handle.directory.name.endswith("aws-attack")


def test_runs_never_overwrite_even_same_label_and_instant(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    first = store.start(kind=RunKind.EVAL, label="blog", id_hint="blog-run0", now=_NOW)
    second = store.start(kind=RunKind.EVAL, label="blog", id_hint="blog-run0", now=_NOW)

    assert first.directory != second.directory
    assert second.directory.name.endswith("-2")
    assert first.directory.is_dir() and second.directory.is_dir()


def test_write_artifact_model_writes_yaml_and_records_it(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    handle.write_artifact(SPEC_FILENAME, RunLineage(model="claude-opus-4-8"))

    spec_path = handle.directory / SPEC_FILENAME
    assert spec_path.is_file()
    assert "claude-opus-4-8" in spec_path.read_text(encoding="utf-8")

    # the record now lists the artifact on disk (complete-or-partial visibility)
    record = RunRecord.model_validate_json(
        (handle.directory / RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    )
    assert record.artifacts == [SPEC_FILENAME]


def test_write_artifact_str_is_verbatim(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)
    handle.write_artifact("notes.txt", "partial output before crash")
    assert (handle.directory / "notes.txt").read_text(encoding="utf-8") == (
        "partial output before crash"
    )


def test_write_artifact_dedupes_repeated_name(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)
    handle.write_artifact(SPEC_FILENAME, RunLineage(model="a"))
    handle.write_artifact(SPEC_FILENAME, RunLineage(model="b"))  # re-extract overwrites
    assert handle.record.artifacts == [SPEC_FILENAME]
    assert "b" in (handle.directory / SPEC_FILENAME).read_text(encoding="utf-8")


def test_write_cost_persists_breakdown_and_summary(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    handle.write_cost(_ledger_with_one_call("1.50"))

    cost_path = handle.directory / COST_FILENAME
    assert cost_path.is_file()
    body = cost_path.read_text(encoding="utf-8")
    assert "extractor" in body  # per-agent breakdown is surfaced
    assert handle.record.total_cost_usd == Decimal("1.50")
    assert handle.record.num_llm_calls == 1


def test_finalize_sets_terminal_state(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)
    later = datetime(2026, 6, 6, 10, 16, 0, tzinfo=UTC)

    handle.finalize(RunStatus.SHIPPED, metrics={"completeness": 0.9}, now=later)

    record = RunRecord.model_validate_json(
        (handle.directory / RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    )
    assert record.status is RunStatus.SHIPPED
    assert record.ended_at == later
    assert record.metrics == {"completeness": 0.9}


def test_finalize_is_idempotent_first_status_wins(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    handle.finalize(RunStatus.SHIPPED)
    handle.finalize(RunStatus.INTERRUPTED, halt_reason="signal")  # stray late call

    record = RunRecord.model_validate_json(
        (handle.directory / RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    )
    assert record.status is RunStatus.SHIPPED
    assert record.halt_reason is None


def test_writes_are_best_effort_oserror_swallowed_and_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    def _boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    with caplog.at_level(logging.WARNING):
        # none of these may raise even though every write now fails
        handle.write_artifact(SPEC_FILENAME, RunLineage(model="a"))
        handle.finalize(RunStatus.CRASHED, halt_reason="boom")

    assert any("run-store" in r.message for r in caplog.records)


def test_real_vs_eval_separated_by_root(tmp_path: Path) -> None:
    real_root = tmp_path / "runs"
    eval_root = tmp_path / "eval-runs"
    real = RunStore(real_root).start(kind=RunKind.EXTRACT, label="x", now=_NOW)
    measured = RunStore(eval_root).start(kind=RunKind.EVAL, label="blog", now=_NOW)

    assert real_root in real.directory.parents
    assert eval_root in measured.directory.parents
    assert real.directory.parent != measured.directory.parent


def test_update_lineage_merges_known_fields(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(
        kind=RunKind.EXTRACT,
        label="x",
        lineage=RunLineage(input_ref="https://x", code_version="abc123"),
        now=_NOW,
    )
    handle.update_lineage(model="claude-opus-4-8")

    record = RunRecord.model_validate_json(
        (handle.directory / RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    )
    assert record.lineage.model == "claude-opus-4-8"
    assert record.lineage.code_version == "abc123"  # preserved
    assert record.lineage.input_ref == "https://x"


# --- Trajectory primitives: content-addressed blobs + JSONL append (Item 1) ---


def test_write_blob_is_content_addressed_and_dedups(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    sha1 = handle.write_blob("the system prompt")
    sha2 = handle.write_blob("the system prompt")  # identical -> same hash, stored once

    assert sha1 == sha2
    blob_path = handle.directory / BLOBS_DIRNAME / f"{sha1}.txt"
    assert blob_path.is_file()
    assert blob_path.read_text(encoding="utf-8") == "the system prompt"

    sha3 = handle.write_blob("the blog body")  # distinct content -> distinct blob
    assert sha3 != sha1
    assert (handle.directory / BLOBS_DIRNAME / f"{sha3}.txt").is_file()

    # the blobs directory is registered once in the record (complete-vs-partial visibility)
    assert handle.record.artifacts.count(f"{BLOBS_DIRNAME}/") == 1


def test_append_jsonl_appends_one_line_per_record(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    handle.append_jsonl(TRAJECTORY_FILENAME, RunLineage(model="a"))
    handle.append_jsonl(TRAJECTORY_FILENAME, RunLineage(model="b"))

    lines = (handle.directory / TRAJECTORY_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["model"] == "a"
    assert json.loads(lines[1])["model"] == "b"
    # the file is registered exactly once no matter how many lines are appended
    assert handle.record.artifacts.count(TRAJECTORY_FILENAME) == 1


def test_write_blob_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    def _boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    with caplog.at_level(logging.WARNING):
        sha = handle.write_blob("x")  # must not raise even though the write fails

    assert sha  # the hash is still returned so the trajectory line can reference it
    assert any("run-store" in r.message for r in caplog.records)


def test_append_jsonl_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RunStore(tmp_path)
    handle = store.start(kind=RunKind.EXTRACT, label="x", now=_NOW)

    def _boom(self: Path, *args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _boom)

    with caplog.at_level(logging.WARNING):
        handle.append_jsonl(TRAJECTORY_FILENAME, RunLineage(model="a"))  # must not raise

    assert any("run-store" in r.message for r in caplog.records)
