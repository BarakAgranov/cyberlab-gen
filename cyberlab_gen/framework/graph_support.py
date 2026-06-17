"""Shared LangGraph node mechanics for the pipeline coordinators (extract + plan).

Both the extract orchestrator (:mod:`cyberlab_gen.framework.orchestrator`) and the plan-refinement
coordinator (:mod:`cyberlab_gen.framework.plan_orchestrator`) wrap each node in a stage span
(ADR 0041) and route via a node-decided destination parked on the state — LangGraph discards
mutations made inside conditional-edge functions, so every routing decision is made in a *node* and
the resolved destination is read by a pure edge. These thin, state-generic wrappers are the
genuinely-shared graph mechanics; the larger ``Stage``/``Node`` abstraction that will absorb both
coordinators is deferred to the first *parallel* node (the Phase-3 Generators,
``dev/phase-2-seams.md`` ③.1) — sharing only what is shareable now keeps that later refactor clean
without copy-pasting a ~390-line node closure across the two coordinators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.tracing_setup import stage_span

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def traced_async[S](name: str, fn: Callable[[S], Awaitable[S]]):
    """Wrap an async node so its execution is a pipeline-stage span (ADR 0041).

    Generic over the state type ``S`` so both ``PipelineState`` and ``PlanPipelineState`` reuse it.
    The return type is left to inference so it stays the precise coroutine type LangGraph's
    ``add_node`` expects (an ``Awaitable`` annotation is too broad to match).
    """

    async def _wrapped(state: S) -> S:
        with stage_span(name):
            return await fn(state)

    return _wrapped


def traced_sync[S](name: str, fn: Callable[[S], S]):
    """Wrap a sync node so its execution is a pipeline-stage span (ADR 0041). Generic over ``S``."""

    def _wrapped(state: S) -> S:
        with stage_span(name):
            return fn(state)

    return _wrapped


__all__ = ["traced_async", "traced_sync"]
