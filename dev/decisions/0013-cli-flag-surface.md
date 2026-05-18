# ADR 0013: CLI flag surface — five deviations from the Task 7 brief

**Status:** Accepted (Phase 0 Task 7)
**Date:** 2026-05-18
**Decider:** Task 7 implementation agent

## Context

Phase 0 Task 7 (`dev/phase-briefs/phase-0-agent-brief.md` lines 361–404)
ships the `cyberlab-gen` CLI as four typer-based stubs plus
`--version`/`--help` and the `cli/output.py` formatter. A
pre-implementation audit of the brief surfaced five places where it is
silent, inconsistent, or under-specified versus the architecture
documents. Per CLAUDE.md's authority gradient (architecture > other
docs > this file > `dev/decisions/`), each deviation is recorded here
rather than resolved silently.

## Decisions

### 1. `generate` declares both `--interactive` and `--auto`

`architecture.md §2.1` (lines 282–284) specifies both modes;
`--interactive` is the default. The brief's `[--auto] ...` inline
example omits `--interactive`.

Both flags are declared on the `generate` verb as `typer.Option(False,
...)`. A post-parse check raises `typer.BadParameter` if both are set;
if neither is set, the mode defaults to `--interactive` per the
architecture. Phase 0 verbs are stubs, so the mode value is never
consumed — but the parse contract is real and tested.

### 2. `--max-llm-cost` is a global option

The brief (work item 4) literally says "Wire up `--max-llm-cost` as a
**global** option that creates a `CostLedger` with the cap."
`provider-interface.md §5.4` specifies the flag without locating it on
a particular verb. Architecturally, only `generate` (Phase 3+) and
`fix` (Phase 5+) spend on LLMs; `validate` and `telemetry submit` do
not.

Implemented as global, on the top-level Typer `@app.callback()`. The
ledger is built once per invocation and attached to `ctx.obj` so any
verb can read it. The acknowledged wart: `validate` and `telemetry
submit` accept the flag and ignore it. This is the brief's literal
shape and resolves naturally when the LLM-spending verbs start
spending in Phase 1+.

"Cost ledger surfaces in tests" (brief phrasing) is interpreted as: a
CLI test passes `--max-llm-cost 5.00`, invokes a verb stub, and
asserts `ctx.obj.cost_ledger.cap_usd == Decimal("5.00")`.

### 3. `--state-dir` is implemented as a global option

The brief's Inputs paragraph names it ("local state for the
`--state-dir` override") but the Work items 1–6 do not restate it.
`LocalState(root=Path(state_dir))` already supports the override
(`cyberlab_gen/state/local_state.py:55`).

Implemented as a global option on the top-level callback.
`tests/integration/test_cli.py` uses it to inject `tmp_path` into the
CLI instead of monkey-patching `Path.home()`. The flag is small,
useful, and brief-anticipated.

### 4. `--version` uses `importlib.metadata.version("cyberlab-gen")`

The brief says "prints `0.0.1` and exits 0" and "No discretion on:
`--version` returns `0.0.1`." `pyproject.toml:3` already declares
static `version = "0.0.1"`, so `importlib.metadata.version` returns
exactly the value the brief requires.

Implemented via `importlib.metadata.version("cyberlab-gen")` rather
than a hardcoded string. The runtime value matches the brief today and
stays in sync with `pyproject.toml` on future bumps without touching
the CLI module. The brief's intent (the right value gets printed) is
satisfied; the strict-letter reading (a hardcoded literal) is not.

Note: `implementation-plan.md §3.1` line 131 says `--version` returns
`0.0.0`, conflicting with the Phase-0 brief and `pyproject.toml`. The
brief/pyproject win.

### 5. `--debug` ships now as a global option

`coding-conventions.md §6.3` (line 248) requires the `--debug` flag
that controls whether stack traces appear in user-facing output. The
brief omits it.

Implemented as a global option on the top-level callback. The flag
flips a module-level `_DEBUG` toggle in `cli/output.py`; the
traceback-printing branch in `output.print_error` is exercised by a
unit test. Phase 0 stubs never raise, so the flag has no observable
effect on stub output — but the scaffolding is in place per §6.3.

The "internal traces always written to the run's structured report"
half of §6.3 is deferred — there is no run-report runner in Phase 0;
that lands with the orchestrator in Phase 1+.

## Consequences

- The brief's literal flag surface (`--max-llm-cost`, `--auto`,
  `--version`) is preserved as-written for tests and operator muscle
  memory.
- Three additional global options exist: `--state-dir`, `--debug`, and
  the implicit `--interactive` (alongside `--auto`).
- `validate` and `telemetry submit` silently accept `--max-llm-cost`.
  This is documented in the help text ("global option, used by LLM-spending
  verbs only") and is the natural shape until those verbs ship.
- `--debug`'s observable effect is unit-tested but not stub-tested, since
  no Phase-0 production path raises.

### 6. `__init__.py` does not re-export `main`; pyproject points at the module path

The brief's work item 1 says "Create `cyberlab_gen/cli/__init__.py`
exposing the `main` entry point." A literal re-export
(`from cyberlab_gen.cli.main import main` inside `__init__.py`) creates
a name collision: after `__init__.py` runs, the attribute
`cyberlab_gen.cli.main` resolves to the **function** rather than the
**module**. Test code that needs to reset module-level state (the
`LAST_INVOCATION_CONTEXT` test hook) cannot then access the submodule by its
canonical attribute path, and pyright's strict mode flags every such
access.

`cli/__init__.py` is therefore left as a docstring-only stub. The
console script in `pyproject.toml` points at the canonical path
`cyberlab_gen.cli.main:main`. The brief's intent (the entry point is
exposed and `cyberlab-gen --version` works) is preserved; the
strict-letter reading (re-export in `__init__.py`) is not.

## Brief revision recommended

The next Phase-0-brief sweep (or Phase 1's brief that introduces real
verb logic) should:

1. Add `--interactive` to the inline flag list of `generate`.
2. Either drop the `--state-dir` mention from the Task 7 Inputs paragraph
   or add it as an explicit Work item.
3. Note that "hardcode `0.0.1`" is shorthand for "the value from
   `pyproject.toml` at this commit"; `importlib.metadata.version` is the
   preferred idiom.
4. Add `--debug` to the global flag surface per `coding-conventions.md §6.3`.
5. Update the `generate` landing-phase parenthetical from
   "Phase 5 (full integrated generation)" to **Phase 3** (AWS pipeline) —
   per `implementation-plan.md:477` `cyberlab-gen generate <url>` first
   produces a runnable AWS lab at the end of Phase 3.
6. Reword work-item 1 from "exposing the `main` entry point" to "registers
   the console script for ``main()``" — the literal re-export pattern
   creates a function/module name conflict.

## References

- `docs/architecture.md §2.1`
- `docs/coding-conventions.md §6.3`
- `docs/provider-interface.md §5.4`
- `docs/implementation-plan.md §6, §8` (Phase 3 generate, Phase 5 fix/validate/telemetry)
- `dev/phase-briefs/phase-0-agent-brief.md` Task 7 block (lines 361–404)
- `cyberlab_gen/state/local_state.py` (`LocalState(root=...)`)
- `cyberlab_gen/providers/cost_ledger.py` (`CostLedger(run_id, cap_usd)`)
- `CLAUDE.md` authority gradient
