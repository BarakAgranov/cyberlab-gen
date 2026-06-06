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

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from langgraph.checkpoint.base import BaseCheckpointSaver


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
        # Handle HttpUrl & friends (see module docstring) — local checkpoints only.
        saver.serde = JsonPlusSerializer(pickle_fallback=True)
        yield saver


__all__ = ["open_sqlite_checkpointer"]
