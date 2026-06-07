# 0053 — The run store is the single persistence authority (it reads the checkpoint / last-emitted state directly)

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `pipeline.md §3.5`, `architecture.md §2.3` (local-state layout).
**Reconciles ADR 0039** (the run store) **and ADR 0040** (the LangGraph checkpointer) by
designating the authority and the data source between them. This is item **G1** of the
A1–G1 design-alignment plan.

## Context

Two persistence systems exist, and they don't compose on a mid-graph abort:

- **ADR 0039 — the run store** (`~/.cyberlab-gen/runs/<run-id>/`) persists a run's artifacts
  (AttackSpec, jury verdict, enrichment result, cost breakdown) "on every exit." But it persists
  from the run's *result* — effectively an in-memory `last_state` that LangGraph populates on a
  **clean** graph return.
- **ADR 0040 — the LangGraph checkpointer** (`~/.cyberlab-gen/checkpoints/<run-id>/`) persists each
  completed super-step's state so `resume` can continue.

The seam: a mid-node abort — e.g. a `TransientFailure` during the jury after a clean, expensive
extraction — never produces a clean return, so the in-memory `last_state` the run store reads is
empty or partial, and **the run store drops the partial run's artifacts** — even though the
checkpointer already wrote the completed-node state to disk. Two overlapping "remember the partial
run" mechanisms with a gap between them is fragile by construction, and it is exactly where a
finished-but-unshipped spec can be lost.

## Decision

**The run store is the single persistence authority.** Its source of truth for "what this run
produced," including a partial run, is the **checkpoint (the completed-node state) or the graph's
last-emitted state, read directly** — never an in-memory field that is only populated on a clean
graph return. The checkpointer's role narrows to what it is uniquely for: persist super-step state
so `resume` can continue. The run store reads from that same persisted state, so a partial run is
captured **once, by one authority**, on every exit path — clean, halted, budget-aborted, or crashed.

There is no second, parallel "remember the partial run" path.

## Consequences

- **Docs updated in this pass:** `pipeline.md §3.5` gains a "Persistence authority: the run store"
  paragraph stating the single-authority rule and the read-the-checkpoint-directly data source.
- **Code is a separate, later work-stream:** the run store's persistence path must read the
  checkpoint / graph last-emitted state rather than an in-memory `last_state` only set on clean
  return; the second partial-run path is removed.
- **No change to ADR 0039's run-store structure or ADR 0040's checkpointer surface** — G1 only
  designates the authority and the data source *between* them, which neither ADR pinned. `resume`
  (ADR 0040) is unaffected (same checkpoint); the run store now also reads that checkpoint on abort
  instead of relying on a clean return.
- Composes with the F1 work-stream: the run record the run store persists is the authoritative
  artifact the eval reads (`eval.md §7.4`), so a single authority also means a single source of
  truth for measurement.
