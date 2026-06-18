# 0039 — Artifact persistence: the run store (the system's memory of what it produces)

**Date:** 2026-06-06
**Phase:** 1 (operational-foundation pass, outcome #5 — expanded)
**Architecture refs:** `architecture.md §2.3` (local-state layout: `runs/` per-run
working directories), `pipeline.md §3.6` (run report), `§3.7` (run/checkpoint
directories). Builds on ADR 0037 (the persisted per-run log), ADR 0038
(`CostLedger` / `CostReportBlock`), ADR 0028 (eval incremental archive). The
checkpointer/resume half of outcome #5 is recorded separately in ADR 0040; this ADR
covers the artifact-store structure and the exit/persistence guarantees.

## Context

cyberlab-gen's pipeline produces artifacts that are *themselves the work product* —
an `AttackSpec`, a jury verdict, an enrichment result (and, in later phases, a lab
manifest, IaC, attack scripts, detection rules). For a system whose purpose is to
evaluate and improve those artifacts, they must be inspectable, comparable across
runs, and never lost. The pre-existing behaviour fell short on every count:

- **Real `extract`** wrote `attack-spec.yaml` to the cwd **only on ship**, last-write-
  wins (a re-run silently overwrote the previous spec). A halt, a `BudgetExceeded`, a
  `KeyboardInterrupt`, or any crash left **nothing** — and the verb did not catch
  `KeyboardInterrupt` at all. The jury verdict and enrichment never touched disk; the
  per-agent/per-model cost breakdown (`CostLedger.to_report_block()`) was discarded.
- **Eval** archived per-blog (ADR 0028), but `run_once` caught only `CyberlabGenError`,
  so a crash or Ctrl-C in the **first** blog left `eval/reports/` empty. Specs went to
  a flat `eval/reports/specs/<blog>-run<n>.yaml` that overwrote on re-run; the cost
  breakdown was likewise discarded.

It was therefore possible to spend real money and end with nothing to read — the exact
failure outcome #5 exists to make impossible.

## Decision

A new module `cyberlab_gen/state/run_store.py` provides a `RunStore` / `RunHandle`
abstraction that is the single, reusable persistence surface for **both** entry points
(real `extract` and eval). It is built around four committed properties:

### 1. Every stage's output is persisted, always — complete or partial

`RunStore.start()` creates the run directory and writes `run.json` (status `running` +
lineage) **before the first LLM call**. Artifacts stream to disk as produced; each write
re-flushes `run.json` so the record always reflects what is on disk (`RunRecord.artifacts`
lists the files that exist — an inspector tells complete from partial at a glance). A
`finally` / signal handler calls `RunHandle.finalize()` on **every** exit path (success,
halt, cost-abort, interrupt, crash). All writes are **best-effort**: an `OSError` is
logged and swallowed so persistence can never mask the original error. Net effect: it is
structurally impossible to run the pipeline and end with nothing to read.

### 2. Runs are identifiable and never silently overwritten

The run id is `<UTC-timestamp>-<slug>` (`slug` = host+last-path-segment for an `extract`
URL, `<blog_id>-run<index>` for eval). A same-instant collision gets a numeric suffix.
Each run directory holds **all** of that run's artifacts together, so any run is a
complete inspectable record that can be diffed against another. Nothing overwrites.

### 3. Real runs vs. eval runs are separated — by *location*

The store is constructed with a `root`. A real `extract` deliverable lives under
`LocalState.runs_dir` (`~/.cyberlab-gen/runs/<run-id>/`); eval (measurement/experiment)
runs live in the repo at `eval/reports/runs/<run-id>/`, alongside the existing aggregate
`gen*.yaml` reports. Same code, different pile — the separation is where artifacts live,
extending ADR 0028's `provider_backed`/rotation-generation idea from a *flag* to a
*place*.

### 4. Room for provenance/lineage (Layer 4) — designed for, not fully built

`RunLineage` carries the fields a future lineage system needs (`input_ref`,
`input_hash`, `model`, `extractor_version`, `prompt_version`, `code_version`),
populated best-effort now and left `None` where not yet known. Building the full lineage
capture is deferred; the run-directory structure leaves room for it.

### Run directory contents

```
<run-id>/
  run.json          RunRecord: id, kind, label, status, halt_reason, timing,
                    lineage, cost summary, list of artifacts written
  spec.yaml         the AttackSpec (complete or partial)
  jury-verdict.yaml the jury verdict (when produced)
  enrichment.yaml   the enrichment result (when produced)
  cost.yaml         the full CostReportBlock (per-agent / per-model / per-provider
                    breakdown + per-call entries — the data the report discarded)
  run.log           the per-run DEBUG log (ADR 0037)
  trajectory.jsonl  the per-round agent trajectory: an append-only ordered event log of
                    every agent call's content + each routing decision (ADR 0098)
  blobs/            content-addressed input blobs trajectory.jsonl references by hash, so
                    a large constant input (system prompt, blog body) is stored once (ADR 0098)
```

The list is illustrative, not exhaustive or closed: a verb may add its own files (the `plan`
verb's `planner-refusal.yaml`; the extract checkpointer's `checkpoint.sqlite`). The contract is
that every file on disk is registered in `RunRecord.artifacts` (so an inspector tells complete
from partial), and that **only the run store writes the run dir** — `trajectory.jsonl` and
`blobs/` are written through `RunHandle` (`append_jsonl` / `write_blob`), so adding them
*extends* the single persistence authority (ADR 0053/0068), it does not open a second writer.
The trajectory is written incrementally as each round completes, inheriting this ADR's
best-effort / always-something-to-read discipline (a halt/crash keeps the rounds already done).

### Scope: both entry points, this ADR's wiring

This ADR introduces the module and its design. The CLI `extract` wiring + exit
guarantees (SIGINT/SIGTERM handler, `KeyboardInterrupt` capture, the deliverable in cwd
kept unchanged and mirrored into the run dir) and the eval wiring + first-blog-crash fix
(the `try/finally` that archives even a non-`CyberlabGenError` escape) land in the same
operational-foundation pass under this ADR.

## Alternatives considered

- **Move the `extract` deliverable into the run dir (drop the cwd write).** Rejected:
  users expect the spec in their working folder. Decision (this session): *mirror* —
  keep the cwd `attack-spec.yaml` deliverable unchanged AND persist the full run record
  under `runs/`.
- **Persist from inside the LangGraph nodes.** Rejected: the orchestrator surface is
  ADR-0023-locked, and node-level writes would couple persistence to that locked code.
  Artifacts are captured at the entry-point boundary instead (the terminal/partial
  `PipelineState` is available there before the halt errors are raised), with the
  checkpointer (ADR 0040) covering mid-node-crash state.
- **Restructure eval around run dirs (retire the flat reports).** Rejected as too large
  and test-breaking for this pass. Decision: *additive* — keep the `gen*.yaml` aggregate
  reports (cross-run analysis; tests rely on them) and add per-run dirs beside them.
- **Raise on a failed persistence write.** Rejected: persistence must never mask the
  original error. Writes are best-effort (log + swallow), matching ADR 0033/0035.

## Consequences

- New `cyberlab_gen/state/run_store.py` (`RunStore`, `RunHandle`, `RunRecord`,
  `RunLineage`, `RunKind`, `RunStatus`) exported from `state`; unit-tested for run-id
  non-overwrite, partial/complete writes, finalize idempotency, best-effort OSError
  handling, and real-vs-eval root separation.
- A repo-wide ruff config addition (`flake8-type-checking.runtime-evaluated-base-classes`
  for the Pydantic model bases) replaces the scattered `# noqa: TC001` workarounds — the
  model field types now stay runtime-importable by configuration, not by hand.
- The CLI and eval wiring (same pass) make persistence happen on every exit path and
  surface the discarded `CostReportBlock`. The checkpointer/resume guarantee is ADR 0040.
