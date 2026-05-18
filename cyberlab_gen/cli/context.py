"""CLI invocation context — the object attached to ``typer.Context.obj``.

The top-level Typer callback (:mod:`cyberlab_gen.cli.main`) builds a
:class:`CliContext` once per invocation, after parsing the global
options (``--max-llm-cost``, ``--state-dir``). Verb handlers reach it
through ``ctx.obj`` (Typer/Click idiom). Phase 0 stubs do not consume
the context, but tests assert that it is built correctly given the
global flag values — this pins the wiring contract before Phase 1
adds real verb logic.

The dataclass is intentionally small. Future fields (provider
registry, run id, telemetry toggle) land here as the orchestrator
grows in Phase 1+.
"""

from dataclasses import dataclass

from cyberlab_gen.providers import CostLedger
from cyberlab_gen.state import LocalState


@dataclass(frozen=True)
class CliContext:
    """Per-invocation CLI state, attached to ``typer.Context.obj``."""

    state: LocalState
    cost_ledger: CostLedger
