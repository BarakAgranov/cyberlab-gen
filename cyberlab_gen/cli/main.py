"""Top-level Typer CLI for ``cyberlab-gen``.

Architectural source: ``docs/architecture.md §2.1`` (the four verbs:
``generate``, ``validate``, ``fix``, ``telemetry submit``). Phase 0
ships them as stubs that print "not yet implemented" and exit 1; real
verb logic lands in later phases per ``implementation-plan.md``:

* ``generate`` — Phase 3 (AWS generation pipeline, line 477)
* ``validate`` — Phase 5 (validation runner, line 767)
* ``fix`` — Phase 5 (Repair Agent, line 720)
* ``telemetry submit`` — Phase 5 (telemetry submission flow, line 758)

Global option surface (decided in ADR 0013):

* ``--version`` — print the value from ``importlib.metadata`` and exit 0.
* ``--max-llm-cost USD`` — global per Task 7 brief; constructs the
  per-invocation :class:`CostLedger`. Phase-0 verbs do not consume it,
  but tests assert the cap is plumbed through.
* ``--state-dir PATH`` — override :class:`LocalState` root, used by
  integration tests to inject ``tmp_path``.
* ``--debug`` — flips :mod:`cyberlab_gen.cli.output`'s ``_debug_enabled``
  toggle per ``coding-conventions.md §6.3``.

``generate`` declares both ``--interactive`` (default per
``architecture.md §2.1``) and ``--auto``; passing both raises
``typer.BadParameter``. The stubs do not consume the mode.
"""

from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import Annotated

import typer

from cyberlab_gen.cli import output
from cyberlab_gen.cli.context import CliContext
from cyberlab_gen.providers import CostLedger
from cyberlab_gen.state import LocalState

# Test hook: integration tests read this after ``runner.invoke`` to assert
# that the global flags were plumbed into the per-invocation context.
# Production code MUST NOT read this. Reset to ``None`` between tests by the
# fixture in ``tests/integration/test_cli.py``.
last_invocation_context: CliContext | None = None


app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Generate runnable, validated cyber labs from security writeups.",
)
telemetry_app = typer.Typer(
    no_args_is_help=True,
    help="Telemetry operations (submit queued reports).",
)
app.add_typer(telemetry_app, name="telemetry")


def _version_callback(value: bool) -> None:
    """Print the installed package version and exit 0 when ``--version`` is set.

    Uses ``importlib.metadata.version`` so the runtime value tracks
    ``pyproject.toml`` across releases without touching this module. ADR 0013
    records the deviation from the brief's "hardcode 0.0.1" wording.
    """
    if value:
        typer.echo(metadata.version("cyberlab-gen"))
        raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def _main(  # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    max_llm_cost: Annotated[
        float | None,
        typer.Option(
            "--max-llm-cost",
            metavar="USD",
            help=(
                "Cap total LLM spend for this invocation. Used by LLM-spending verbs "
                "(generate, fix); accepted but ignored by validate and telemetry submit."
            ),
        ),
    ] = None,
    state_dir: Annotated[
        Path | None,
        typer.Option(
            "--state-dir",
            help=(
                "Override the local-state root (default: ~/.cyberlab-gen). Primarily a test hook."
            ),
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help=(
                "Surface stack traces in user-facing errors. "
                "Internal traces are written to the run report regardless."
            ),
        ),
    ] = False,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Build the per-invocation :class:`CliContext` and stash it on ``ctx.obj``.

    Runs before every verb (``invoke_without_command=True`` means the
    callback also fires when no subcommand is supplied; Typer then
    prints ``--help`` because ``no_args_is_help=True``). ``--version``
    short-circuits via its eager callback before this body runs.
    """
    global last_invocation_context
    output.set_debug(debug)
    state = LocalState(root=state_dir) if state_dir is not None else LocalState()
    cap_usd: Decimal | None = Decimal(str(max_llm_cost)) if max_llm_cost is not None else None
    ledger = CostLedger(run_id="cli-session", cap_usd=cap_usd)
    cli_ctx = CliContext(state=state, cost_ledger=ledger)
    ctx.obj = cli_ctx
    last_invocation_context = cli_ctx


_GENERATE_STUB_MESSAGE = (
    "not yet implemented in Phase 0; this verb lands in Phase 3 (AWS generation pipeline)"
)
_VALIDATE_STUB_MESSAGE = (
    "not yet implemented in Phase 0; this verb lands in Phase 5 (validation runner)"
)
_FIX_STUB_MESSAGE = "not yet implemented in Phase 0; this verb lands in Phase 5 (Repair Agent)"
_TELEMETRY_SUBMIT_STUB_MESSAGE = (
    "not yet implemented in Phase 0; this verb lands in Phase 5 (telemetry submission flow)"
)


@app.command()
def generate(
    url: Annotated[
        str,
        typer.Argument(help="Blog URL to generate a lab from."),
    ],
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="Pause at typed-artifact interrupts for user review. Default mode.",
        ),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Run without interrupts except for budget-overrun.",
        ),
    ] = False,
) -> None:
    """Generate a runnable lab from a blog URL (stub in Phase 0)."""
    if interactive and auto:
        raise typer.BadParameter(
            "--interactive and --auto are mutually exclusive",
            param_hint="--interactive / --auto",
        )
    del url  # Phase-0 stub: argument accepted but unused.
    output.print_info(_GENERATE_STUB_MESSAGE)
    raise typer.Exit(code=1)


@app.command()
def validate(
    lab_dir: Annotated[
        Path,
        typer.Argument(help="Path to an already-generated lab directory."),
    ],
) -> None:
    """Run mechanical validation against a lab directory (stub in Phase 0)."""
    del lab_dir
    output.print_info(_VALIDATE_STUB_MESSAGE)
    raise typer.Exit(code=1)


@app.command()
def fix(
    lab_dir: Annotated[
        Path,
        typer.Argument(help="Path to a lab directory to debug."),
    ],
) -> None:
    """Interactive REPL for post-generation lab debugging (stub in Phase 0)."""
    del lab_dir
    output.print_info(_FIX_STUB_MESSAGE)
    raise typer.Exit(code=1)


@telemetry_app.command("submit")
def telemetry_submit() -> None:
    """Submit queued telemetry reports after sanitization (stub in Phase 0)."""
    output.print_info(_TELEMETRY_SUBMIT_STUB_MESSAGE)
    raise typer.Exit(code=1)


def main() -> None:
    """Console-script entry point declared in ``pyproject.toml``."""
    app()
