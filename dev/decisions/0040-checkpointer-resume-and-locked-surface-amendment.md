# 0040 — Pipeline resume via a LangGraph checkpointer (ADR-0023 surface amendment)

**Date:** 2026-06-06
**Phase:** 1 (operational-foundation pass, outcome #5 — resume half)
**Architecture refs:** `pipeline.md §3.7` (run/checkpoint directories — "pipeline-resume
snapshots"), `architecture.md §2.3` (`checkpoints/` in the local-state layout). Builds on
ADR 0023 (the locked orchestrator surface) and ADR 0039 (the run store the checkpoint
lives beside). Sibling of the persistence half (ADR 0039).

## Context

ADR 0039 guarantees a run's artifacts are *persisted* on every exit. It does not make a
validly-in-progress run *resumable*: a crash mid-node (e.g. a `TransientFailure` during
the jury after a clean, expensive extraction) discards the completed nodes' work, so a
re-run pays for extraction again. The pipeline is a LangGraph `StateGraph`; LangGraph's
native checkpointer persists each completed super-step's state and resumes from it. The
gap is purely that `graph.compile()` was called with no checkpointer.

This is the most sensitive change in the pass: it touches the **ADR-0023-locked**
`build_pipeline`/`run_pipeline` surface, which CLAUDE.md and ADR 0023 forbid modifying
silently. This ADR records the amendment explicitly.

## Decision

### Locked-surface amendment (ADR 0023)

Two **additive, optional** changes to `framework/orchestrator.build_pipeline`:

1. A new keyword-only parameter `checkpointer: BaseCheckpointSaver | None = None`,
   passed straight to `graph.compile(checkpointer=checkpointer)`. `None` (the default)
   compiles exactly as before — no checkpointing, no behaviour change.
2. The returned `run` callable (now typed by a `PipelineRun` Protocol) gains a
   keyword-only `thread_id: str | None = None` and accepts `state=None`:
   - with a checkpointer, `thread_id` namespaces this run's checkpoints;
   - `run(None, thread_id=…)` **resumes** that thread from its last completed node
     (LangGraph's canonical resume is invoking with `None` input);
   - a normal run passes the initial `PipelineState` as before.

No existing caller breaks: every current call is `run(state)` with no checkpointer, which
is unchanged. `run_pipeline` is untouched. The amendment is purely opt-in.

### Serializer: pickle fallback for rich Pydantic types

`PipelineState` carries an `AttackSpec`, whose `source` block uses Pydantic `HttpUrl`.
LangGraph's default msgpack serializer calls `model_dump()` in **Python** mode and cannot
encode `HttpUrl` (verified: `TypeError: Type is not msgpack serializable`). We enable the
serializer's `pickle_fallback` (`JsonPlusSerializer(pickle_fallback=True)`), which
round-trips the full typed state losslessly (verified by test). The single construction
point is `framework/checkpointing.open_sqlite_checkpointer`, an async context manager that
opens an `AsyncSqliteSaver` and sets that serde.

**Security note.** `pickle_fallback` deserializes with `pickle`. These checkpoints are
**local, code-created files inside the run's own directory** (`<run-dir>/checkpoint.sqlite`),
never fetched from an untrusted source — the same trust boundary as the rest of the run
store. Acceptable for local resume; flagged here so a future networked-checkpoint feature
revisits it.

### Wiring (opt-in, no Protocol churn)

Checkpointing is a `PipelineExtractRunner`-specific capability, enabled by the caller once
a run directory exists (`runner.enable_checkpointing(<run-dir>/checkpoint.sqlite,
thread_id=<run-id>)`). The `ExtractRunner` Protocol and every test fake are untouched (the
caller `isinstance`-checks for the concrete runner). Both entry points enable it:
`cli/extract.run_extract` and `eval/runner/runner.run_once`.

**A fresh thread per drive.** Each `_drive` call (the first run and every interactive
feedback re-run) uses a distinct thread (`<run-id>-<seq>`), so a re-run is always a fresh
graph run and never *accidentally* resumes a previously-completed graph. The crashed run's
checkpoints persist under the run dir, keyed by their thread, for a future resume.

### Scope: checkpointer now, `--resume` flag deferred

Per sign-off, this lands the checkpointer + resume *capability* (state survives a crash and
is resumable, proven by test). The user-facing `--resume <run-id>` entry point is a
follow-up; the run id is recorded in `run.json` (ADR 0039) so that future flag can map a
run to its checkpoint thread.

## Alternatives considered

- **In-memory `MemorySaver`.** Rejected: it does not survive a process crash, so it cannot
  meet the resume goal. A persistent `AsyncSqliteSaver` (new dep
  `langgraph-checkpoint-sqlite`) is required.
- **Narrow `PipelineState` to JSON-only types (drop `HttpUrl`).** Rejected: it would weaken
  the artifact schema for a serializer detail; `pickle_fallback` is local-only and
  non-invasive.
- **Thread the run id through `ExtractRunner.run`.** Rejected: it widens the ADR-0024 seam
  and forces every fake to change. The `enable_checkpointing` capability method keeps the
  Protocol intact.
- **One shared thread per run handle.** Rejected: an interactive feedback re-run would then
  resume the completed first graph instead of re-extracting. A fresh thread per drive avoids
  that footgun.

## Consequences

- New dep `langgraph-checkpoint-sqlite` (+ `aiosqlite`). New module
  `framework/checkpointing.py`. `build_pipeline` gains `checkpointer`; the run callable gains
  `thread_id` and accepts `None` (resume). `PipelineExtractRunner.enable_checkpointing`;
  both entry points opt in, writing `<run-dir>/checkpoint.sqlite`.
- New tests: a mid-node crash leaves a checkpoint and resuming re-runs only the failed node
  (also the `PipelineState` serde round-trip the risk note called for); the no-checkpointer
  default is unchanged.
- The locked ADR-0023 surface is amended **only** as described here; `run_pipeline` and all
  routing/halt behaviour are unchanged.
