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

    from cyberlab_gen.schemas.attack_spec import AttackSpec


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


# --- serde registration: no unregistered/blocked warnings (ADR 0066) -------
#
# The framework checkpoint types are registered with the serializer's allowlist, so they
# round-trip via the REGISTERED msgpack path — no "Deserializing unregistered type … will be
# blocked in a future version" warning, and (because an explicit allowlist BLOCKS unlisted
# types) no "Blocked deserialization" either. The HttpUrl-bearing AttackSpec still goes via
# pickle_fallback. These tests drive the routing paths that populate every registered type.


@pytest.fixture
def _serde_warn_msgs(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Capture every serde warn-message (bypassing the module-level once-dedup)."""
    import langgraph.checkpoint.serde.jsonplus as jsonplus

    msgs: list[str] = []

    def _record(seen: object, key: object, msg: str, *args: object) -> None:
        del seen, key, args
        msgs.append(msg)

    monkeypatch.setattr(jsonplus, "_warn_once", _record)
    return msgs


def _assert_clean(msgs: list[str]) -> None:
    assert not any("unregistered" in m for m in msgs), msgs  # registered path, no warning
    assert not any("Blocked" in m for m in msgs), msgs  # allowlist complete, no collateral block


def _bad_spec() -> AttackSpec:
    return make_spec(facets=["target:bogus_unknown_cloud"])  # static-schema-invalid


def _ungrounded_spec() -> AttackSpec:
    # NOTE: the external_api field is the CVE ``cvss_score`` (``ProvenanceFloat``), NOT
    # ``severity`` (``Provenance[Severity]``): an ad-hoc ``Provenance[<custom enum>]`` is not
    # picklable, so a CVE-severity spec crashes the checkpointer's pickle_fallback (a pre-existing
    # latent bug surfaced by this batch; recorded in ADR 0066, out of the serde item's scope).
    # ``ProvenanceFloat`` is a module-level alias and pickles cleanly.
    from cyberlab_gen.schemas.attack_spec import CveReference, ExternalRefsBlock
    from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource
    from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceFloat, ProvenanceString

    spec = make_spec(facets=["target:aws"])
    spec.external_references = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-9999",  # type: ignore[arg-type]
                description=ProvenanceString(
                    value="v",
                    source=ProvenanceSource.BLOG_EXPLICIT,
                    citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§2")],
                ),
                cvss_score=ProvenanceFloat(
                    value=9.8,
                    source=ProvenanceSource.EXTERNAL_API,  # no trace -> search-before-claim
                    citations=[
                        CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§2"),
                        CitationBlock(
                            kind=CitationKind.EXTERNAL_API_RESPONSE, reference="nvd:CVE-2024-9999"
                        ),
                    ],
                ),
            )
        ]
    )
    return spec


def _round_trip(tmp_path: Path, ext: object, jury: object) -> PipelineState | None:
    db = tmp_path / "checkpoint.sqlite"

    async def _drive() -> None:
        async with open_sqlite_checkpointer(db) as saver:
            run = build_pipeline(
                extractor=ext,  # type: ignore[arg-type]
                validator=make_validator(),
                jury=jury,  # type: ignore[arg-type]
                checkpointer=saver,
            )
            await run(PipelineState(blog_content="blog", source_summary="url=..."), thread_id="t-0")

    asyncio.run(_drive())
    return read_latest_pipeline_state(db)  # alist() deserializes EVERY super-step's checkpoint


def test_clean_run_round_trips_with_no_serde_warnings(
    tmp_path: Path, _serde_warn_msgs: list[str]
) -> None:
    ext = FakeExtractor([make_spec(facets=["target:aws"])])
    jury = FakeJury([make_verdict(Verdict.APPROVE)])
    recovered = _round_trip(tmp_path, ext, jury)
    _assert_clean(_serde_warn_msgs)
    # the registered types reconstruct as the real typed objects, not raw dicts
    assert recovered is not None
    assert recovered.status is PipelineStatus.SHIPPED
    assert recovered.grounding is not None and recovered.verdict is not None
    assert recovered.enrichment is not None


def test_structural_retry_path_round_trips_with_no_serde_warnings(
    tmp_path: Path, _serde_warn_msgs: list[str]
) -> None:
    # Exercises StaticSchemaResult/StaticSchemaCode + RefinementFeedback(STRUCTURAL_RETRY)/FeedbackKind.
    ext = FakeExtractor([_bad_spec(), make_spec(facets=["target:aws"])])
    jury = FakeJury([make_verdict(Verdict.APPROVE)])
    recovered = _round_trip(tmp_path, ext, jury)
    _assert_clean(_serde_warn_msgs)
    assert recovered is not None and recovered.status is PipelineStatus.SHIPPED


def test_grounding_retry_path_round_trips_with_no_serde_warnings(
    tmp_path: Path, _serde_warn_msgs: list[str]
) -> None:
    # Exercises GroundingResult/GroundingCode (with findings) + RefinementFeedback(GROUNDING_RETRY).
    ext = FakeExtractor([_ungrounded_spec(), make_spec(facets=["target:aws"])])
    jury = FakeJury([make_verdict(Verdict.APPROVE)])
    recovered = _round_trip(tmp_path, ext, jury)
    _assert_clean(_serde_warn_msgs)
    assert recovered is not None and recovered.status is PipelineStatus.SHIPPED
