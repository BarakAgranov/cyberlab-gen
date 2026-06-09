# 0068 — One shared run persistence/lineage service; the billed-model invariant has a single home

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch A, item ①.2; [SHARED-ROOT])
**Architecture refs:** `architecture.md §1.5` (the framework records provenance; the LLM never
authors its own model id), ADR 0039/0053 (the run store is the single persistence authority), ADR
0065 (the framework stamps the billed model). Source: investigation `0004 §1.1` (S33), `0003`
cross-ref ADR 0065; the live defect is investigation `0002 §1.5`.

## Context

The run-persistence choreography — write the per-stage artifacts (spec / jury verdict /
enrichment), populate lineage, finalize `run.json` — was implemented **twice**: once in
`cli/extract.py` (`_persist_run` → `_persist_from_state` + `_populate_lineage`, with
`_billed_extractor_model` / `_stamp_billed_model`) and again, independently, in
`eval/runner/runner.py::_persist_run_dir`.

The CLI path correctly records the **billed** provider model (from the cost ledger) on every exit
path. The eval re-implementation did **not**: `_persist_run_dir` called
`handle.update_lineage(model=str(meta.model), …)` — the model's **self-report** — and wrote the
spec without billed-model stamping. On the clean-ship path this was masked (the production
`PipelineExtractRunner` pre-stamps `result.spec`, ADR 0065), but on the **halt / crash / interrupt**
paths `result` is `None`, so the code fell back to the unstamped partial `state.spec` and recorded
whatever the model self-reported into both `lineage.model` and the persisted spec — the exact
ADR-0065 / investigation-0002 §1.5 defect, still live in the sibling. The root cause is structural:
the billed-model invariant lived in two parallel call sites, so it drifted. Phase 2's `generate`
verb would have been a third copy.

## Decision

**Extract a single shared service — `cyberlab_gen/state/run_persistence.py` — and de-duplicate, do
NOT patch the eval copy in place.** It owns:

- `billed_model(ledger, *, agent_label=EXTRACTOR)` — the authoritative billed-model reader (the
  former CLI-private `_billed_extractor_model`, generalised with an `agent_label` parameter so
  Phase-2 generators reuse it).
- `stamp_billed_model(spec, ledger)` — stamps `extraction_metadata.model` from the ledger.
- `persist_pipeline_artifacts(handle, *, state, shipped_spec, ledger, content_hash)` — writes the
  per-stage artifacts + lineage, stamping the billed model onto **whichever** spec it persists
  (`shipped_spec` when present, else the partial `state.spec`).

Both the CLI (`_persist_run`) and the eval (`_persist_run_dir`) now call
`persist_pipeline_artifacts`. The eval leak is fixed **by construction**: the spec it persists is
always billed-stamped and `lineage.model` always comes from the ledger, on every exit path.

What stays caller-side: terminal-status resolution and `handle.finalize`. Those genuinely differ —
the CLI classifies from `sys.exc_info()` in a `finally`; the eval passes an explicit `RunStatus` —
so the shared service deliberately does not own them (the "thin core, callers keep status" shape).

## Alternatives considered

- **Patch the eval `_persist_run_dir` in place** (stamp + read the ledger there) — rejected. That
  leaves two copies of the invariant; copy-patching is exactly what let the bug exist and what
  Phase-2's third copy would inherit.
- **A fuller service that also owns status resolution + finalize** — rejected for now. It would have
  to absorb the two divergent status taxonomies (`PipelineStatus` vs `RunStatus`, a separate
  Tier-4 item) immediately, a much larger blast radius. The thin core fixes the correctness defect
  with the smallest change; status consolidation can follow.

## Consequences

- The eval run record (and persisted spec) record the **billed** model on halt/crash, never the
  self-report — pinned by `test_run_once_records_billed_model_not_self_report_on_halt` (red before
  the change) and the `persist_pipeline_artifacts` unit tests (`tests/unit/state/`).
- CLI behaviour is unchanged: its persistence now flows through the shared service; the existing
  ADR-0065 tests were repointed at the shared functions and still pass.
- ~80 lines of duplicated CLI persistence code are deleted (`_persist_from_state`,
  `_populate_lineage`, `_billed_extractor_model`, `_stamp_billed_model`).
- Phase 2's `generate` verb inherits the correct billed-model behaviour by calling the same seam.
- **No `docs/` edit** — the docs already mandate framework-recorded model provenance (ADR 0065);
  this removes a code path that violated it on the eval sibling.
