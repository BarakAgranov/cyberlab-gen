"""LangGraph checkpointer construction for pipeline resume (ADR 0040).

Centralises the one subtlety in checkpointing the pipeline: ``AttackSpec`` (and the
``ExtractionResult`` that wraps it) carry rich Pydantic types — notably ``HttpUrl`` —
that LangGraph's default msgpack serializer cannot encode (it calls ``model_dump()``
in Python mode, leaving ``HttpUrl`` objects). We enable the serializer's
``pickle_fallback`` so the full typed ``PipelineState`` round-trips losslessly.

Security note: ``pickle_fallback`` deserializes with ``pickle``. These checkpoints are
**local, code-created files** living inside the run's own directory (never fetched from
an untrusted source), so the pickle-trust boundary is the same as the rest of the run
store. Recorded in ADR 0040.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from langgraph.checkpoint.base import BaseCheckpointSaver

    from cyberlab_gen.framework.orchestrator import PipelineState

logger = logging.getLogger(__name__)

#: The framework state-channel types registered with the checkpoint serializer's msgpack
#: allowlist (ADR 0066). These ride the PipelineState channels and serialize via the
#: EXT_PYDANTIC_V2 / Enum ext paths; registering them removes the "Deserializing unregistered
#: type … will be blocked in a future version" warning every run logged, and pins them to the
#: registered (not pickle-fallback) path. Listed as ``(module, name)`` string tuples — NOT
#: imported classes — so this module keeps its lazy import graph (it deliberately never imports
#: the orchestrator/validators/jury/enrichment at module scope; the ext_hook ``import_module``s
#: them on read). Nested models (StaticSchemaFinding, GroundingFinding, JuryFieldFeedback,
#: JuryScores) ride as plain dicts inside their parent's ``model_dump`` payload and need NOT be
#: listed; the ``AttackSpec``/``ExtractionResult`` subtree carries ``HttpUrl`` and still goes via
#: ``pickle_fallback`` (so it is intentionally absent from the msgpack allowlist).
#:
#: MAINTENANCE OBLIGATION: passing an explicit allowlist flips the serializer out of permissive
#: "allow-all-with-warning" mode, so any *new* msgpack-serialized top-level PipelineState channel
#: type added later MUST be added here or it will be silently BLOCKED (returned as raw data,
#: failing resume). The no-warning round-trip tests in ``test_checkpointing.py`` guard this.
_REGISTERED_CHECKPOINT_TYPES: tuple[tuple[str, str], ...] = (
    ("cyberlab_gen.validators.static_schema_validator", "StaticSchemaResult"),
    ("cyberlab_gen.validators.static_schema_validator", "StaticSchemaCode"),
    ("cyberlab_gen.validators.grounding_validator", "GroundingResult"),
    ("cyberlab_gen.validators.grounding_validator", "GroundingCode"),
    ("cyberlab_gen.agents.extractor_jury.schema", "Verdict"),
    ("cyberlab_gen.agents.extractor_jury.schema", "JuryVerdict"),
    ("cyberlab_gen.framework.enrichment", "EnrichmentResult"),
    ("cyberlab_gen.framework.orchestrator", "PipelineStatus"),
    ("cyberlab_gen.framework.orchestrator", "FeedbackKind"),
    ("cyberlab_gen.framework.orchestrator", "RefinementFeedback"),
    ("cyberlab_gen.framework.orchestrator", "PipelineState"),
)


@asynccontextmanager
async def open_sqlite_checkpointer(db_path: Path) -> AsyncGenerator[BaseCheckpointSaver[Any]]:
    """Yield an ``AsyncSqliteSaver`` over ``db_path``, configured for our state.

    The saver holds an open async connection for the duration of the ``async with``;
    the graph must be compiled and driven inside it. The DB file persists after the
    block, so a crashed run's checkpoints remain on disk for a later resume.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        # Register our framework state types (ADR 0066) so they round-trip via the registered
        # msgpack path — no unregistered-type warning, no future-block risk. ``pickle_fallback``
        # stays on for the HttpUrl-bearing AttackSpec subtree (ADR 0040 security note: local,
        # code-created checkpoints only). The explicit allowlist is independent of pickle_fallback.
        saver.serde = JsonPlusSerializer(
            pickle_fallback=True, allowed_msgpack_modules=_REGISTERED_CHECKPOINT_TYPES
        )
        yield saver


def read_latest_pipeline_state(db_path: Path) -> PipelineState | None:
    """Recover the most recent completed-node ``PipelineState`` from a run's checkpoint.

    The run store's single persistence authority (G1, ADR 0053): on every exit path
    — clean ship, ``HALTED_*``, ``BudgetExceeded``, Ctrl-C/SIGTERM, or an unexpected
    crash — the partial (or final) artifacts a run produced are recovered from the
    checkpoint the LangGraph checkpointer already wrote, **not** from an in-memory
    field that is only set on a clean graph return. A mid-graph abort never produces a
    clean return, so reading the checkpoint is what stops the partial ``AttackSpec``
    from being dropped (the L4 bug: a spec already in ``checkpoint.sqlite`` that
    ``run.json`` never listed).

    Returns the latest state across every thread in the file (interactive feedback
    re-runs each get a fresh thread; the newest checkpoint is the run's latest reached
    state), or ``None`` when there is no checkpoint to read. Best-effort: any read or
    deserialization failure is logged and returns ``None`` so persistence never masks
    the run's own outcome.
    """
    if not db_path.exists():
        return None
    try:
        return asyncio.run(_aread_latest_pipeline_state(db_path))
    except Exception:
        # Best-effort: a corrupt/locked checkpoint or an unexpected loop state must not
        # crash the persistence path that is finalizing the run record.
        logger.warning(
            "run-store: could not read latest state from checkpoint %s", db_path, exc_info=True
        )
        return None


async def _aread_latest_pipeline_state(db_path: Path) -> PipelineState | None:
    """Open the checkpoint read-only and reconstruct the newest ``PipelineState``."""
    # Lazy import: the orchestrator imports nothing from this module at load time, but
    # importing PipelineState at module scope would still couple the two; keep it local.
    from cyberlab_gen.framework.orchestrator import PipelineState

    async with open_sqlite_checkpointer(db_path) as saver:
        # ``alist(None)`` yields every thread's checkpoints, globally newest-first; fully
        # consume it inside the open connection before it closes.
        tuples = [ct async for ct in saver.alist(None)]
    if not tuples:
        return None
    channel_values = tuples[0].checkpoint.get("channel_values", {})
    # Keep only the declared state fields (the checkpoint also carries LangGraph-internal
    # channels like ``branch:to:*``); the values are already the deserialized typed
    # objects (AttackSpec, JuryVerdict, ...) thanks to the pickle-fallback serializer.
    fields = set(PipelineState.model_fields)
    filtered = {k: v for k, v in channel_values.items() if k in fields}
    if not filtered:
        return None
    return PipelineState.model_validate(filtered)


__all__ = ["open_sqlite_checkpointer", "read_latest_pipeline_state"]
