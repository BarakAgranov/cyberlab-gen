# 0024 — `extract` verb: the runner seam, the interrupt result shape, and proposal surfacing

**Date:** 2026-06-01
**Phase:** 1 (Task 7)
**Architecture refs:** `pipeline.md §3.1`, `pipeline.md §3.1.1`, `pipeline.md §3.2.5`, `implementation-plan.md §4.2` ("Post-Extractor interrupt"), `architecture.md §1.5`, ADR 0013, ADR 0023

## Decision

The `extract` CLI verb is built on a **Task-7-owned runner seam** rather than
calling `run_pipeline` (ADR 0023) directly:

1. A `RunResult` (`InternalModel`) bundles everything the post-Extractor
   interrupt needs in one typed object: the enriched `AttackSpec`, the
   `value_type_proposals` / `facet_proposals` the Extractor emitted, the
   `material_discrepancies` enrichment recorded, the `PipelineStatus`, the
   `low_jury_confidence` flag, the unresolved feedback, and the
   `estimated_next_stage_cost` (the Planner-stage estimate the §3.2.5 / §3.1.1
   budget check reads). `run_pipeline` returns a `PipelineOutcome` that carries
   neither the proposals nor a next-stage cost estimate; the per-proposal review
   surface (§3.2.5) cannot be built from `PipelineOutcome` alone.

2. An `ExtractRunner` `Protocol` with a single `run(url, *, ledger) -> RunResult`
   method is the seam the verb depends on. The **default** runner
   (`PipelineExtractRunner`) wires Ingestion → the orchestrator and is the
   production path; tests inject a fake `ExtractRunner` that returns a scripted
   `RunResult`. This keeps the verb's deliverable — the four-option menu, the
   per-proposal Accept/Edit loop, the headless guard, the budget-overrun
   interrupt, the YAML write — fully testable **without** a live provider, an
   API key, or recorded LLM cassettes (none of which are in Task 7's scope; the
   agents are exercised against the mock/recorded provider in Task 5's tests).

3. `re_run_with_feedback(feedback, *, ledger) -> RunResult` is a second
   `ExtractRunner` method backing option 2 of the four-option menu (natural-
   language feedback → Extractor re-runs). The free text the user types is
   wrapped before it crosses the seam: the verb never hands raw text to the
   runner as a pipeline-stage payload (`architecture.md §1.5` — no free text
   across stage boundaries; the typed `RunResult` is the return contract and the
   feedback string is a tunnelled prompt addendum the same way ADR 0023's
   `RefinementFeedback.render()` is).

## Context

Two genuinely open questions, decided here rather than guessed:

- **Where do the proposals come from at the interrupt?** Task 5 puts the
  proposals on `ExtractionResult`; Task 6's `run_pipeline` discards them and
  returns only `PipelineOutcome`. Changing `run_pipeline`'s locked return type
  (ADR 0023) to thread proposals through would be a cross-task signature change.
  Instead Task 7 owns a thin runner that has access to the full
  `ExtractionResult` (the default runner drives `build_pipeline` and reads the
  final state's `extraction`) and packs the proposals into `RunResult`. The
  orchestrator stays unchanged.

- **What is the "estimated next-stage spend" the budget-overrun interrupt reads
  (§3.1.1, §3.2.5)?** Phase 1 has no Planner, so there is no real next-stage
  cost yet. The estimate is a runner-supplied figure on `RunResult`
  (`estimated_next_stage_cost`, default `Decimal("0")`); the verb compares
  `ledger.total_usd + estimated_next_stage_cost` against `ledger.cap_usd` and
  interrupts when it would exceed the cap. The default runner supplies `0` in
  Phase 1 (no Planner to estimate); tests supply a non-zero estimate to exercise
  the overrun path in both modes. This keeps the *mechanism* — framework owns the
  cap decision (`provider-interface.md §5.3`), honored in both modes (§3.1.1) —
  real and tested now, with the real estimate dropping in when the Planner ships.

## Alternatives considered

- **Call `run_pipeline` directly and re-derive proposals.** Rejected: proposals
  are not recoverable from `PipelineOutcome`; re-running the Extractor to recover
  them doubles cost and is non-deterministic.
- **Change `run_pipeline` to return proposals.** Rejected: ADR 0023 locked that
  surface this same phase; a thin Task-7 runner avoids the churn and keeps the
  orchestrator single-responsibility.
- **Make the verb itself construct Ingestion + agents + orchestrator inline.**
  Rejected: that hard-wires a live provider into the verb and makes the menu
  logic untestable without an API key. The seam is the testability boundary.

## Consequences

- New module `cyberlab_gen/cli/extract.py` holds `RunResult`, the
  `ExtractRunner` protocol, `PipelineExtractRunner` (the default), the four-option
  and per-proposal menu loops, the `$EDITOR` revalidation loop, and the YAML
  writer. The `extract` verb in `cli/main.py` is thin: parse flags, build the
  runner, delegate.
- The verb writes `attack-spec.yaml` to the working directory (cwd) on Approve /
  auto-accept, per the brief. A `--out` override is **not** added (brief says
  "working directory"; ADR 0013's flag-surface discipline — no flags beyond what
  the brief/architecture name).
- The default `PipelineExtractRunner` needs a configured provider; absent one it
  raises `HardFailure` (the honest "no provider configured" posture, ADR 0011 /
  `provider-interface.md §6.3`). End-to-end provider-backed runs are Task 8 / the
  eval harness; Task 7's tests use the fake runner.
- The auto-accept proposal cap (placeholder 5, `implementation-plan.md §4.2`)
  lives as `DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP` in `cli/extract.py`. In `--auto`,
  proposals beyond the cap are surfaced in the run report as not-accepted; in
  `--interactive` the per-proposal menu has no cap (the user acts on each).
