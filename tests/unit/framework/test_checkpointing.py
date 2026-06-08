"""Tests for reading a run's partial state back from its checkpoint (G1, ADR 0053).

The run store is the single persistence authority: on every exit path it recovers
whatever the pipeline produced — complete or partial — from the LangGraph checkpoint
the checkpointer already wrote, never from an in-memory field that is only populated
on a clean graph return. These tests pin the read mechanism that closes the L4
drop-partial-on-abort bug: a mid-graph abort (Ctrl-C / budget halt / crash) leaves the
last completed node's state in ``checkpoint.sqlite``, and :func:`read_latest_pipeline_state`
recovers it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.framework.checkpointing import (
    open_sqlite_checkpointer,
    read_latest_pipeline_state,
)
from cyberlab_gen.framework.orchestrator import PipelineState, PipelineStatus, build_pipeline
from tests.unit.framework.pipeline_fakes import (
    CrashOnceJury,
    FakeExtractor,
    FakeJury,
    make_spec,
    make_validator,
    make_verdict,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_read_latest_recovers_partial_spec_after_midgraph_abort(tmp_path: Path) -> None:
    # A mid-graph abort (the jury raises after extract + validate completed) leaves a
    # partial AttackSpec in the checkpoint, but NO clean graph return — so the in-memory
    # "last state" the run store used to read was never set, and the spec was dropped
    # (the L4 bug). read_latest_pipeline_state must recover it straight from the checkpoint.
    db = tmp_path / "checkpoint.sqlite"
    ext = FakeExtractor([make_spec(facets=["target:aws"])])
    jury = CrashOnceJury([make_verdict(Verdict.APPROVE)])

    async def _drive_until_abort() -> None:
        async with open_sqlite_checkpointer(db) as saver:
            run = build_pipeline(
                extractor=ext, validator=make_validator(), jury=jury, checkpointer=saver
            )
            with pytest.raises(RuntimeError, match="boom"):
                await run(
                    PipelineState(blog_content="blog", source_summary="url=..."), thread_id="t-0"
                )

    asyncio.run(_drive_until_abort())

    recovered = read_latest_pipeline_state(db)
    assert recovered is not None
    assert recovered.spec is not None
    assert recovered.spec.facets == ["target:aws"]


def test_read_latest_recovers_full_state_on_clean_run(tmp_path: Path) -> None:
    # On a clean ship the latest checkpoint is the final, enriched terminal state — the
    # same read path recovers it (so persistence reads one authority for both halt + ship).
    db = tmp_path / "checkpoint.sqlite"
    ext = FakeExtractor([make_spec(facets=["target:aws"])])
    jury = FakeJury([make_verdict(Verdict.APPROVE)])

    async def _drive() -> None:
        async with open_sqlite_checkpointer(db) as saver:
            run = build_pipeline(
                extractor=ext, validator=make_validator(), jury=jury, checkpointer=saver
            )
            await run(PipelineState(blog_content="blog", source_summary="url=..."), thread_id="t-0")

    asyncio.run(_drive())

    recovered = read_latest_pipeline_state(db)
    assert recovered is not None
    assert recovered.spec is not None
    assert recovered.enrichment is not None  # the terminal state carries enrichment
    assert recovered.status is PipelineStatus.SHIPPED


def test_read_latest_returns_none_for_missing_db(tmp_path: Path) -> None:
    # No checkpoint at all (the run died before any node completed) → nothing to recover.
    assert read_latest_pipeline_state(tmp_path / "absent.sqlite") is None
