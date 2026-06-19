# 0100 — Post-Planner interrupt: shared interrupt module, structural edit-revalidation, run-scoped facet promotion

**Date:** 2026-06-18
**Phase:** 2 (Task 8)
**Architecture refs:** `pipeline.md §3.2.8` (post-Planner interrupt — the two review surfaces),
`§3.1.1` (the four-option typed-artifact menu; structural edit-revalidation; budget-overrun
both modes), `schema.md §4.16` (proposal lifecycle; promotion gated on ship; per-run cap),
`§4.13` (facet authorship split — the Planner owns `runtime:*` + lab-derived
`lab_class_signal:*`), `architecture.md §1.5` (no free text across stage boundaries; the
framework routes), `§2.1`/ADR 0096 (`extract`/`plan` are developer / eval commands, **not** the
user surface), ADR 0024 (the `extract` runner-seam + interrupt), ADR 0044 (propose→accept→overlay
loop), ADR 0050/0062 (promotion gated on ship; over-cap is bounded steering), ADR 0099 (the
generic accept path; Planner facet proposals captured-but-not-promoted; the §6 manifest Layer-1
owned deferral).

## Context

Task 8 builds the post-Planner interrupt — the §3.2.8 two-surface review (the LabManifest
four-option menu + per-facet-proposal Accept/Edit) — mirroring the Phase-1 post-Extractor
interrupt (ADR 0024). Three sub-decisions were genuinely open and are recorded here rather than
guessed; the fourth (overlay scope) is the open Task-7 item, now due where promotion is wired.

## Decision

1. **Build the interrupt on the `plan` verb, mirroring ADR 0024.** `plan` is the dev/eval home that
   exercises the Planner stage in isolation (ADR 0096), exactly as `extract` is for the Extractor;
   so the post-Planner interrupt lives on `plan`, just as the post-Extractor interrupt lives on
   `extract`. `plan` becomes **interactive-by-default with `--auto` bypass** (mirroring `extract`),
   reconciling its Task-6 "non-interactive in Phase 2" placeholder. The Feedback option re-runs the
   Planner with the user's free text folded into the Planner's `preferences` prompt addendum (the
   typed `PlanRunResult` is the return contract — the same tunnelling as ADR 0024's extract
   feedback; no free text crosses the stage boundary, `§1.5`).

2. **Shared interrupt module (`cli/interrupt.py`) — build at second use.** The genuinely
   artifact-agnostic interrupt machinery (the menu enums, the four-option prompt parameterized by
   which agent re-runs, the YAML round-trip, the structural edit-revalidation loop, the per-proposal
   Accept/Edit loop, the auto-accept cap) is extracted into `cli/interrupt.py`; both verbs consume
   it. This is the same "generalize at the second use" discipline as ADR 0089's tool-provider hook
   and ADR 0086's stamp dispatch — and the direction-neutral single home (vs. `plan` importing from
   `extract`, the rejected backwards dependency). The `extract` migration is **test-guarded**:
   extract's existing tests stay green and its public/tested names remain as **thin re-export
   wrappers** (a mild smell, fine to keep Phase-1 tests untouched now — flagged for cleanup when
   extract is next touched, not to ossify).

3. **Structural edit-revalidation — NOT "Layer 1/2".** `§3.2.8`/`§3.1.1` (the authority) say edits
   are *structurally* re-validated; the brief's "revalidated through Layer 1/2" is reconciled **down**
   to structural per the authority gradient: a manifest `$EDITOR` edit is `LabManifest.model_validate`
   + version refusal, a facet-proposal edit is `ProposedFacet.model_validate`. There is no manifest
   Layer-1 facet-membership gate to revalidate against in any case (ADR 0099 §6 owned deferral).
   **Awareness (by design, not a bug):** a human manifest-edit is *not* re-run through the semantic
   cross-check, so a human can edit-in a cross-check violation (e.g. a dangling `identifier_source`)
   and ship — the human is the authority at the interrupt (`§3.1.1`: "semantic correctness of user
   edits is the user's responsibility"). Re-running the cross-check on an edit as a **warning** is a
   future item (needs the deferred `Finding`-base severity, `dev/phase-2-seams.md §2`), not now.

4. **Run-scoped facet promotion — the ADR-0096 ↔ promote-on-ship reconciliation (the open Task-7
   item).** A multi-surface investigation (a workflow: 4 independent sweeps + 2 adversarial
   verifiers, all confirmed) established: (a) the production overlay default is the **shared**
   per-user `~/.cyberlab-gen/registry-overlay/`, silently chosen — only `--state-dir` (a documented
   test hook) redirects it; (b) `extract --auto` already promotes to it with no human confirmation
   (an existing property); (c) **eval is read-only w.r.t. the global overlay** because the eval
   harness bypasses the verb entirely — it builds the runner and calls `.run()` directly (the
   orchestrator path), only *counting* proposals into the metric, and never reaches the promotion
   helpers (zero overlay-write symbols in `eval/`); (d) no in-code run-kind guard scopes the write
   (`RunKind.EVAL` exists but is never consulted). **Therefore:** `plan` is a dev/eval command (ADR
   0096); its promotion must not silently mutate the shared production vocabulary. `plan`'s
   post-Planner promotion — **both** the interactive accept-on-ship and the `--auto`
   auto-accept-on-ship — targets a **run-scoped overlay = `<run_dir>/registry-overlay`**, never the
   shared `~/.cyberlab-gen/registry-overlay`. The promotion **mechanism** is built + tested
   end-to-end (the real `accept_proposals` → `write_overlay_entry` path, stamped
   `proposed_by=planner` / `proposal_origin=llm_during_planning` / `source_lab=manifest.core.id`);
   only the **target** is scoped. Dedup still runs against the merged-registries snapshot (bundled +
   global overlay) so an already-known facet is skipped. Real production vocabulary promotion
   (writing the shared overlay) remains **`generate`'s** job (Phase 3+), where a real user builds a
   real lab under the full review.

5. **No budget-overrun interrupt this task.** `§3.2.8` surfaces a refined *Generator* cost estimate;
   the Generators are Phase 3, so there is no next-stage estimate to compare against. Deferred to
   when the Generators (and their estimate) exist — `extract` had the identical gap pre-Planner.

## Alternatives considered

- **Mirror `extract` exactly (shared-overlay default for `plan`).** Rejected — makes the dev/eval
  `plan` a second silent writer to the production vocabulary (the user's explicit "not acceptable"),
  and a *concurrency-unsafe* second writer to the same files (the overlay writer's load-merge-write
  is crash-atomic, not concurrency-safe).
- **An in-code `RunKind`/eval write-guard on the shared accept path (the "strongest fix").**
  Rejected *for Task 8 scope*: it touches the shared accept/overlay-write path **and** changes
  `extract`'s (locked Phase-1) behavior. Run-scoping the `plan` target is the tight, plan-only change
  that resolves the new writer cleanly. The broader guard — and `extract`'s own
  shared-overlay-from-a-dev/eval-command property — is a tracked architect item, not Task 8's.
- **`plan` imports the interrupt helpers from `cli/extract`.** Rejected — a backwards dependency
  (the later phase's machinery would be the import *source*); the shared module is the
  direction-neutral single home.

## Consequences

- New `cli/interrupt.py`; `cli/extract` migrated onto it (behavior-identical, test-guarded; thin
  re-export wrappers flagged for cleanup). `cli/plan` gains the interrupt drivers, run-scoped
  promotion, `PlanRunner.re_run_with_feedback`, and a held `registries` snapshot for accept-time
  dedup. The `plan` verb gains `--interactive`/`--auto` + the headless guard.
- `plan` (the dev/eval command) **never** mutates the shared production overlay. A maintainer who
  wants to commit a Planner facet to global vocabulary does so via `generate` (Phase 3+), not `plan`.
- **Owned deferral / interim exposure:** when Task 10 builds the Phase-2 plan eval, it **must**
  mirror the extract-eval pattern (drive the plan runner directly, never `run_plan`'s promotion) so
  eval-plan stays read-only w.r.t. any overlay — exactly as eval-extract does. Recorded so the
  deferral is owned, not emergent.
- **Status-staleness fix on BOTH verbs (adversarial-review finding).** `run_plan`/`run_extract`
  bound the persisted `result` to the *first* run; an interactive Feedback re-run rebinds a *local*
  result that never propagated back, so the `finally` resolved the run-record status/refusal/verdict
  from the **stale first run**. On `plan` (whose orchestrator *returns* terminal states, ADR 0097)
  this misrecorded route-back as `ABORTED` + dropped the refusal, and low-confidence as `SHIPPED`;
  on `extract` (whose orchestrator *raises* halts, so only the confidence flag was exposed) a
  Feedback re-run that flips `low_jury_confidence` misrecorded `SHIPPED` ⇄ `SHIPPED_LOW_CONFIDENCE`.
  Fixed identically: the interrupt drivers return `(path, final_result)` and the verb rebinds
  `result` before the `finally`. RED→GREEN regression tests on both verbs. The deliverable
  (`lab.yaml` / `attack-spec.yaml`) was always correct (the ship path uses the re-run); only the
  run record was wrong.

## Recorded deferrals (owned)

1. **Plan-eval must bypass `run_plan`'s promotion (owner: Task 10).** When the Phase-2 plan eval is
   built, it must mirror the extract-eval pattern — drive the plan runner directly, never the
   promoting verb — so eval-plan stays read-only w.r.t. any overlay (eval is `--auto`; this keeps it
   off both the run-scoped and the shared overlay). Confirmed by the overlay-scope investigation:
   eval-extract is read-only *only* because it bypasses `run_extract` today.

2. **Extract `--auto` shared-overlay reconciliation (owner: architect / Phase-3 `generate` Validator
   work).** The A-decision (run-scoped `plan` promotion; `generate` is the only thing that
   accumulates to the shared overlay) makes `extract`'s current behavior the outlier: `extract`
   — also a dev/eval command (ADR 0096) — still writes the shared production overlay
   (`~/.cyberlab-gen/registry-overlay`) on `--auto`, with no human confirmation, today. Reconcile it
   to run-scoped to match `plan` (and/or add a `RunKind`/measurement write-guard on the shared accept
   path — `RunKind.EVAL` already exists but is not consulted). **Not fixed in Task 8** — it touches
   locked Phase-1 behavior and spans both verbs; this is the recorded owner + interim exposure
   (dev/eval `extract --auto` runs can promote to the production vocabulary until reconciled).

3. **Human-edit-rename facet desync is covered by the ADR-0099 §6 deferral.** At the per-proposal
   Edit, renaming a facet proposal (e.g. `runtime:foo` → `runtime:bar`) promotes the entry under the
   **new** name while the manifest still declares the **old** `runtime:foo` — an unregistered
   manifest facet reference, with nothing in the `plan` pipeline re-checking the link. This is **not
   a new gap**: it is another path into the **same** unwired manifest Layer-1 registry-membership
   gap recorded in ADR 0099 §6 (owner: the Phase-3 `generate` Validator stage), whose check is over
   the **final manifest's** facet references against the resolved registry — so it catches an
   unregistered reference regardless of how it arose (Planner typo, proposed-but-unpromoted, *or*
   human-edit-rename). Confirmed the §6 deferral covers the human-edit-rename case; no Task-8 fix
   (Task 8 re-validates user edits structurally, it is not the producer membership gate — ADR 0099
   §6 explicitly rules Task 8 the wrong owner). The orphaned new-name overlay entry is harmless (an
   extra registered facet nothing references).
