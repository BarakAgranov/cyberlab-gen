"""Local Phoenix/OpenTelemetry tracing for the pipeline (ADR 0041).

cyberlab-gen's agent layer runs on pydantic-ai, which emits OpenTelemetry spans
natively (``Agent.instrument_all``). This module points those spans — plus manual
spans for the LangGraph pipeline stages — at a **local** Arize Phoenix instance so
every LLM call (model, tokens, cost, stop reason, prompt/response, tool calls) and
the stage tree are viewable as traces. Data stays on the machine; there is no cloud
export.

Design guarantees (the run must never crash or block because of tracing):

- **Off by default unless Phoenix is actually there.** ``setup_tracing`` probes the
  endpoint first (a fast TCP check) and no-ops when nothing is listening, so a normal
  run with no Phoenix is completely unaffected and pays no import cost. ``CYBERLAB_GEN_
  TRACING`` overrides: ``off`` disables entirely, ``on`` forces setup, ``auto`` (the
  default) enables only when the endpoint is reachable.
- **Never raises.** A missing observability extra (ImportError) or any setup failure is
  logged and swallowed; tracing is simply disabled.
- **Non-blocking export.** Spans batch in the background and drop silently if the
  collector goes away mid-run.

Native pydantic-ai OTel only — we deliberately do **not** add a second instrumentor
(e.g. the anthropic-SDK one), which would double-count spans, tokens and cost (ADR 0036).

Enable a local Phoenix with::

    docker run -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest

then run any ``extract``/eval command and view traces at ``http://localhost:6006``.
The observability extra must be installed: ``uv sync --extra observability``.
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)

#: Default local Phoenix endpoint (its UI + OTLP-HTTP collector share port 6006).
DEFAULT_PHOENIX_ENDPOINT = "http://localhost:6006"

#: ``off`` / ``on`` / ``auto`` (default). Mirrors the run-log / cost env-var pattern.
_TRACING_ENV = "CYBERLAB_GEN_TRACING"

#: The project's OTel tracer, set once tracing is configured; ``None`` ⇒ disabled, and
#: :func:`stage_span` is a no-op. Module-global so :func:`stage_span` stays import-cheap
#: (the orchestrator imports it unconditionally and must not pull in OTel when off).
_tracer: object | None = None
_configured = False


def setup_tracing(*, endpoint: str | None = None, service_name: str = "cyberlab-gen") -> bool:
    """Configure local Phoenix tracing if it is wanted and reachable; return enabled?.

    Idempotent and best-effort. Called once at each entry point after logging. Returns
    ``True`` when tracing was enabled, ``False`` when it was skipped (off, Phoenix not
    reachable, extra not installed, or a setup error) — in every skip case the caller
    proceeds normally with no tracing.
    """
    global _configured
    if _configured:
        return _tracer is not None

    mode = (os.environ.get(_TRACING_ENV) or "auto").strip().lower()
    if mode == "off":
        _configured = True
        return False

    target = endpoint or os.environ.get("PHOENIX_ENDPOINT") or DEFAULT_PHOENIX_ENDPOINT
    # Probe BEFORE importing anything heavy: when Phoenix is down (the common case) we
    # no-op without paying the OTel import cost. ``on`` forces setup regardless.
    if mode != "on" and not _endpoint_reachable(target):
        logger.info("tracing: Phoenix not reachable at %s; tracing disabled", target)
        _configured = True
        return False

    try:
        _configure_tracing(target, service_name)
    except ImportError:
        logger.warning(
            "tracing: observability extra not installed "
            "(uv sync --extra observability); tracing disabled"
        )
        _configured = True
        return False
    except Exception:  # tracing must never break a run
        logger.warning("tracing: setup failed; continuing without tracing", exc_info=True)
        _configured = True
        return False

    _configured = True
    logger.info("tracing: enabled -> Phoenix at %s (view at %s)", target, target)
    return True


def _configure_tracing(endpoint: str, service_name: str) -> None:
    """Wire pydantic-ai's native OTel spans to the local Phoenix collector."""
    global _tracer
    from opentelemetry import trace
    from phoenix.otel import register  # pyright: ignore[reportMissingImports]  # optional extra
    from pydantic_ai import Agent

    register(
        endpoint=f"{endpoint.rstrip('/')}/v1/traces",
        project_name=service_name,
        set_global_tracer_provider=True,
        auto_instrument=False,  # we instrument pydantic-ai explicitly, nothing else
        batch=True,  # background, non-blocking export
    )
    Agent.instrument_all(True)
    _tracer = trace.get_tracer("cyberlab_gen.pipeline")


def _endpoint_reachable(endpoint: str, *, timeout: float = 0.25) -> bool:
    """Fast TCP check that something is listening at the endpoint's host:port."""
    parsed = urlparse(endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6006
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@contextmanager
def stage_span(name: str) -> Generator[None]:
    """Open a pipeline-stage span (e.g. ``stage.extract``); a no-op when tracing is off.

    Lets the LangGraph stage tree (extract / validate / jury / enrich, plus ingestion)
    appear nested under the agent spans in Phoenix. Costs nothing when tracing is
    disabled — the orchestrator wraps every node with it unconditionally.
    """
    if _tracer is None:
        yield
        return
    # ``_tracer`` is an OTel ``Tracer`` once configured.
    with _tracer.start_as_current_span(f"stage.{name}"):  # type: ignore[attr-defined]
        yield


def reset_tracing_for_tests() -> None:
    """Reset module state so a test can re-exercise :func:`setup_tracing` cleanly."""
    global _tracer, _configured
    _tracer = None
    _configured = False


__all__ = [
    "DEFAULT_PHOENIX_ENDPOINT",
    "reset_tracing_for_tests",
    "setup_tracing",
    "stage_span",
]
