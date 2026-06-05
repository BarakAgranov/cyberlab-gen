# 0037 — Central logging standard: one setup, a persisted run-log file

**Date:** 2026-06-05
**Phase:** 1 (operational-foundation pass, outcome #4)
**Architecture refs:** `coding-conventions.md §6.2` (stdlib logging, per-module
loggers, lazy `%`-format, level taxonomy), `§6.3` (user-facing errors via
`cli.output`, distinct from logging). Operational-foundation principle: no run
should cost money without producing a complete, readable account of what happened.

## Context

The package had **no central logging configuration**: 11 modules create
`logging.getLogger(__name__)` and emit at the documented levels, but nothing
installs a handler, so library logs went to the root logger's default (WARNING,
no destination) and were effectively lost. A run could not be reconstructed from a
file afterward — the core of outcome #4.

## Decision

Add `cyberlab_gen/logging_setup.py` with a single idempotent `setup_logging()`,
invoked once at each process entry point (`cli/main._main` and
`eval/runner/cli.main`). Library modules keep doing only `getLogger(__name__)` —
they never configure handlers (`§6.2`).

- **File handler — always, level DEBUG.** Full detail to a per-run file so the run
  is readable afterward. Filename `run-<UTC-timestamp>[-<run_id>].log`.
- **Console handler — WARNING by default, DEBUG with `--debug`.** Curated
  user-facing messages remain `cli.output`'s job; the console logger only surfaces
  genuine warnings/errors so it doesn't compete with the CLI/eval output streams.
- **Directory is code-created**, never hand-set by the user:
  `$CYBERLAB_GEN_LOG_DIR` if set, else `platformdirs.user_log_dir` (e.g.
  `%LOCALAPPDATA%\cyberlab-gen\Logs`). The eval prints the path to stderr so it is
  discoverable.
- **Idempotent.** Repeated calls (CLI callback + eval main, or tests) do not stack
  handlers; a later `--debug` re-applies the console level only.
- **Test isolation.** The env override plus an autouse fixture
  (`tests/conftest.py`) redirect logs to a tmp dir and reset handlers between
  tests, so the suite never writes into the developer's real log directory.

## Alternatives considered

- **`logging.basicConfig` at import time** — rejected: configuring at import (in a
  library) is the anti-pattern `§6.2`/`§3.3` warns against; config belongs at the
  entry point.
- **Console at INFO** — rejected: INFO stage/per-call chatter would clutter the CLI
  interactive menus and the eval stderr progress stream; INFO lives in the file.
- **Log file under the repo (`eval/reports/logs`) by default** — rejected as the
  default (pollutes the tree / tests); offered via the env override instead, and
  `setup_logging(log_dir=...)` lets a caller opt in.

## Consequences

- New module + tests; `cli/main._main` and `eval/runner/cli.main` call
  `setup_logging`. Subsequent outcomes (#5 persistence, #6 cost visibility) write
  their structured events through this configuration, and the run-log path is one
  of the artifacts the guaranteed-persistence work (#5) surfaces.
