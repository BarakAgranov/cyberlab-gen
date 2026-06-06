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

import sys
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cyberlab_gen.cli import output
from cyberlab_gen.cli.context import CliContext
from cyberlab_gen.logging_setup import setup_logging
from cyberlab_gen.providers import DEFAULT_CATASTROPHE_CEILING_USD, CostLedger
from cyberlab_gen.runtime import persisting_signal_guard
from cyberlab_gen.state import LocalState, RunStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from cyberlab_gen.cli.extract import ExtractRunner

# Test hook: integration tests read this after ``runner.invoke`` to assert
# that the global flags were plumbed into the per-invocation context.
# Production code MUST NOT read this. Reset to ``None`` between tests by the
# fixture in ``tests/integration/test_cli.py``.
last_invocation_context: CliContext | None = None

# Test seam (ADR 0024): the ``extract`` verb builds its pipeline runner through
# this factory. Tests override it to inject a fake ``ExtractRunner`` so the
# interrupt menus are exercised without a live provider. ``None`` → the verb
# builds the production ``PipelineExtractRunner`` (which needs a configured
# provider and raises ``HardFailure`` without one). Reset between tests.
extract_runner_factory: "Callable[[LocalState], ExtractRunner] | None" = None

# Test seam: CliRunner replaces ``sys.stdin`` with a non-TTY stream during
# ``invoke``, so the real ``isatty()`` check can't be driven from a test that
# wants to exercise the interactive menus. ``None`` → use the real check
# (production); ``True``/``False`` → force the TTY verdict. Reset between tests.
stdin_tty_override: bool | None = None


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
    setup_logging(debug=debug)
    state = LocalState(root=state_dir) if state_dir is not None else LocalState()
    # Default to the high catastrophe ceiling (ADR 0038), not "no cap": even without
    # --max-llm-cost a runaway is bounded. --max-llm-cost lets the user set an
    # informed lower limit once per-call costs are visible in the run log.
    cap_usd: Decimal = (
        Decimal(str(max_llm_cost)) if max_llm_cost is not None else DEFAULT_CATASTROPHE_CEILING_USD
    )
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


def _build_extract_runner(state: LocalState, ledger: CostLedger) -> "ExtractRunner":
    """Build the production :class:`PipelineExtractRunner` (or the injected fake).

    The ``extract_runner_factory`` test seam (ADR 0024) lets the CLI tests supply
    a fake runner so the interrupt menus are driven without a live provider. In
    production the factory is ``None`` and this wires Ingestion + the agents +
    Validator Layer 1 onto the orchestrator; the agents resolve a configured
    provider and raise ``HardFailure`` (``provider-interface.md §6.3``) if none
    is configured.

    The production provider is wrapped in a ``CostRecordingProvider`` bound to
    ``ledger`` (ADR 0038) so every billed call is recorded + logged (cost visibility)
    and the cumulative spend is capped mid-run by the catastrophe ceiling carried on
    the ledger. The same ``ledger`` is the one the verb threads to ``run_extract``.
    """
    if extract_runner_factory is not None:
        return extract_runner_factory(state)
    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
    from cyberlab_gen.providers.ranking import build_provider_registry
    from cyberlab_gen.registries.merge import load_merged_registries
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

    registry = build_provider_registry()
    provider = CostRecordingProvider(AnthropicProvider(), ledger, purpose="cli")
    registries = load_merged_registries()
    return PipelineExtractRunner(
        extractor=Extractor(provider=provider, registry=registry, registries=registries),
        validator=StaticSchemaValidator(registries=registries),
        jury=ExtractorJury(provider=provider, registry=registry, registries=registries),
        state=state,
    )


@app.command()
def extract(
    ctx: typer.Context,
    url: Annotated[
        str,
        typer.Argument(help="Blog URL to extract an AttackSpec from."),
    ],
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="Pause at the post-Extractor interrupt for review. Default mode.",
        ),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Run without interrupts (except budget-overrun); auto-accept proposals.",
        ),
    ] = False,
) -> None:
    """Extract a validated ``attack-spec.yaml`` from a blog URL (Phase 1)."""
    from cyberlab_gen.cli.extract import run_extract
    from cyberlab_gen.errors import CyberlabGenError
    from cyberlab_gen.framework.orchestrator import JuryRejectionError

    if interactive and auto:
        raise typer.BadParameter(
            "--interactive and --auto are mutually exclusive",
            param_hint="--interactive / --auto",
        )
    cli_ctx = ctx.obj
    assert isinstance(cli_ctx, CliContext)
    runner = _build_extract_runner(cli_ctx.state, cli_ctx.cost_ledger)
    # The run store persists every run's artifacts on every exit path (ADR 0039);
    # the directory is created by the code, never set up by the user by hand.
    cli_ctx.state.ensure_runs_dir()
    run_store = RunStore(cli_ctx.state.runs_dir)
    # ``stdin_tty_override`` is a test seam: CliRunner swaps ``sys.stdin`` for a
    # non-TTY stream during invoke, so the interactive menus can't be driven via
    # the real isatty() check. Tests set this to True to exercise them; None →
    # the real check (production).
    stdin_is_tty = sys.stdin.isatty() if stdin_tty_override is None else stdin_tty_override
    try:
        with persisting_signal_guard():
            written = run_extract(
                url=url,
                interactive=interactive,
                auto=auto,
                runner=runner,
                ledger=cli_ctx.cost_ledger,
                stdin_is_tty=stdin_is_tty,
                run_store=run_store,
            )
    except KeyboardInterrupt as exc:  # Ctrl-C / SIGINT / (converted) SIGTERM
        output.print_error("interrupted; the partial run was saved to the run store", exc=exc)
        raise typer.Exit(code=130) from exc
    except JuryRejectionError as exc:
        output.print_error(f"extraction halted: {exc}", exc=exc)
        raise typer.Exit(code=1) from exc
    except CyberlabGenError as exc:
        output.print_error(f"extraction failed: {exc}", exc=exc)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:  # headless --interactive rejection (pipeline.md §3.1)
        output.print_error(str(exc), exc=exc)
        raise typer.Exit(code=2) from exc
    if written is None:
        raise typer.Exit(code=1)  # aborted / out-of-scope-auto / budget-abort


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
