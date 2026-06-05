"""Integration tests for the ``cyberlab-gen`` CLI (Phase 0 Task 7).

Verifies the typer-based scaffold from :mod:`cyberlab_gen.cli.main`:

* ``--version`` prints the value from ``importlib.metadata`` (today: ``0.0.1``).
* ``--help`` lists all four verbs.
* Each verb stub prints a "not yet implemented" message and exits 1.
* The global flag surface (``--max-llm-cost``, ``--state-dir``, ``--debug``)
  plumbs into the per-invocation :class:`CliContext` correctly.
* ``generate`` rejects passing both ``--interactive`` and ``--auto``.
* ``output.print_error`` includes the traceback only when ``--debug`` is on.

Flag-plumbing assertions read ``cli.main.last_invocation_context`` and
``cli.output._debug_enabled`` — both are test hooks, reset by an autouse fixture.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cyberlab_gen.cli import main as cli_main
from cyberlab_gen.cli import output as cli_output
from cyberlab_gen.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:  # pyright: ignore[reportUnusedFunction]
    """Reset module-level test hooks between cases.

    Marked unused-ignored because pyright's strict mode does not recognize
    pytest's fixture discovery; this function is collected by pytest at
    runtime.
    """
    cli_main.last_invocation_context = None
    cli_main.extract_runner_factory = None
    cli_output.set_debug(False)


def test_version_flag_exits_zero_with_pyproject_version() -> None:
    """``--version`` prints the value from ``importlib.metadata`` and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.stdout


def test_help_lists_all_four_verbs() -> None:
    """``--help`` mentions ``generate``, ``validate``, ``fix``, and ``telemetry``."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for verb in ("generate", "validate", "fix", "telemetry"):
        assert verb in result.stdout


def test_generate_stub_exit_code_and_message() -> None:
    """``generate`` stub exits 1 with a Phase-3 landing message."""
    result = runner.invoke(app, ["generate", "http://example.test"])
    assert result.exit_code == 1
    assert "not yet implemented" in result.stdout
    assert "Phase 3" in result.stdout


def test_validate_stub_exit_code_and_message(tmp_path: Path) -> None:
    """``validate`` stub exits 1 with a Phase-5 landing message."""
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "not yet implemented" in result.stdout
    assert "Phase 5" in result.stdout


def test_fix_stub_exit_code_and_message(tmp_path: Path) -> None:
    """``fix`` stub exits 1 with a Phase-5 landing message."""
    result = runner.invoke(app, ["fix", str(tmp_path)])
    assert result.exit_code == 1
    assert "not yet implemented" in result.stdout
    assert "Phase 5" in result.stdout


def test_telemetry_submit_stub_exit_code_and_message() -> None:
    """``telemetry submit`` stub exits 1 with a Phase-5 landing message."""
    result = runner.invoke(app, ["telemetry", "submit"])
    assert result.exit_code == 1
    assert "not yet implemented" in result.stdout
    assert "Phase 5" in result.stdout


def test_max_llm_cost_flag_sets_ledger_cap() -> None:
    """``--max-llm-cost 5.00`` plumbs into ``ctx.obj.cost_ledger.cap_usd``."""
    result = runner.invoke(app, ["--max-llm-cost", "5.00", "generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_main.last_invocation_context is not None
    assert cli_main.last_invocation_context.cost_ledger.cap_usd == Decimal("5.00")


def test_max_llm_cost_flag_omitted_defaults_to_catastrophe_ceiling() -> None:
    """Omitting ``--max-llm-cost`` defaults the cap to the catastrophe ceiling (ADR 0038).

    Not ``None``: even without a user-set cap, a runaway must be bounded by the high
    backstop. ``--max-llm-cost`` lets the user lower it to an informed value.
    """
    from cyberlab_gen.providers import DEFAULT_CATASTROPHE_CEILING_USD

    result = runner.invoke(app, ["generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_main.last_invocation_context is not None
    assert cli_main.last_invocation_context.cost_ledger.cap_usd == DEFAULT_CATASTROPHE_CEILING_USD


def test_state_dir_flag_overrides_local_state_root(tmp_path: Path) -> None:
    """``--state-dir`` plumbs into ``LocalState.root`` on the built context."""
    result = runner.invoke(app, ["--state-dir", str(tmp_path), "generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_main.last_invocation_context is not None
    assert cli_main.last_invocation_context.state.root == tmp_path


def test_state_dir_flag_omitted_uses_home_dotdir() -> None:
    """Omitting ``--state-dir`` falls back to ``Path.home() / '.cyberlab-gen'``."""
    result = runner.invoke(app, ["generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_main.last_invocation_context is not None
    assert cli_main.last_invocation_context.state.root == Path.home() / ".cyberlab-gen"


def test_debug_flag_flips_output_module_state() -> None:
    """``--debug`` sets ``cli.output._debug_enabled`` to ``True`` for the invocation."""
    result = runner.invoke(app, ["--debug", "generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_output.is_debug() is True


def test_debug_flag_default_is_false() -> None:
    """Without ``--debug``, ``cli.output._debug_enabled`` stays ``False``."""
    result = runner.invoke(app, ["generate", "http://example.test"])
    assert result.exit_code == 1
    assert cli_output.is_debug() is False


def test_generate_rejects_both_interactive_and_auto() -> None:
    """Passing both ``--interactive`` and ``--auto`` to ``generate`` is a usage error."""
    result = runner.invoke(app, ["generate", "http://example.test", "--interactive", "--auto"])
    assert result.exit_code != 0
    combined_output = result.stdout + (result.stderr if result.stderr_bytes else "")
    assert "mutually exclusive" in combined_output


def test_print_error_without_debug_omits_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``print_error`` writes only the clean message when debug is ``False``."""
    cli_output.set_debug(False)
    try:
        raise ValueError("boom")
    except ValueError as exc:
        cli_output.print_error("a clean error", exc=exc)
    captured = capsys.readouterr()
    assert "a clean error" in captured.err
    assert "Traceback" not in captured.err
    assert "ValueError" not in captured.err


def test_print_error_with_debug_includes_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``print_error`` appends the traceback when debug is ``True``."""
    cli_output.set_debug(True)
    try:
        raise ValueError("boom")
    except ValueError as exc:
        cli_output.print_error("a clean error", exc=exc)
    captured = capsys.readouterr()
    assert "a clean error" in captured.err
    assert "Traceback" in captured.err
    assert "ValueError" in captured.err
