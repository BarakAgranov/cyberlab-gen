# 0023 — Extractor pipeline: LangGraph state machine, retry-vs-refinement split, and the Layer-1 → retry seam

> **Refined by [ADR 0051](0051-one-orchestrator-owned-mechanical-validator-stack.md).** The
> "Extractor's internal hallucination/search-before-claim budget … the orchestrator never sees"
> (decision pt 1) folds into one orchestrator-owned mechanical-validator stack — the orchestrator
> owns that routing too. The retry-vs-refinement split itself (Layer-1/structural → retry; jury
> `revise` → refinement) is unchanged.

**Date:** 2026-06-01
**Phase:** 1 (Task 6)
**Architecture refs:** `pipeline.md §3.1`, `pipeline.md §3.2.2`–§3.2.4, `pipeline.md §3.3`, `validation.md §6.4`, `validation.md §6.10`, `architecture.md §1.5`, `architecture.md §1.7`

## Decision

The Phase-1 generation pipeline (Ingestion → Extractor → Validator-Layer-1 →
Extractor-Jury → enrichment) is assembled as a LangGraph `StateGraph` over a
single typed `PipelineState` (a `pydantic` model used as the graph channel). Two
distinct failure mechanisms are encoded explicitly, per `architecture.md §1.7`
and `validation.md §6.10`:

1. **Layer 1 (structural) → the Extractor's *retry* mechanism.** When
   Validator Layer 1 reports `passed=False`, the orchestrator re-runs the
   Extractor with the Layer-1 findings as structured *structural* feedback,
   bounded by a per-stage **structural-retry budget** (default 3 total attempts,
   `architecture.md §1.7`). This budget is the orchestrator's own counter,
   *separate* from the Extractor's internal hallucination/search-before-claim
   budget (ADR 0021) and the call surface's malformed-output budget (ADR 0018) —
   those handle failures the orchestrator never sees. On Layer-1 retry
   exhaustion the pipeline **halts** with `ValidationError`; it never escalates
   to the refinement coordinator.

2. **Jury `revise` (quality) → the *refinement* coordinator.** When the
   Extractor-Jury returns `revise`, the `RefinementCoordinator` re-runs the
   Extractor with the jury's field-targeted feedback wrapped in a
   `UserFeedback`-like `RefinementFeedback` object, bounded by a per-agent
   iteration cap (placeholder 3, `implementation-plan.md §4.2`). On cap
   exhaustion the pipeline distinguishes the last verdict (`pipeline.md §3.2.3`):
   `revise` → ship the last AttackSpec with `low_jury_confidence=true` and the
   unresolved feedback in the run report; `reject` → halt.

The orchestrator owns all routing; the agents and the validator only produce
content/judgments/findings (`architecture.md §1.5`).

## Context

Phase 1 has exactly one agent (the Extractor) plus its jury, but the retry-vs-
refinement split must be built correctly now so Phase 2+ inherits a sound shape
(`implementation-plan.md §4.2`). The brief mandates LangGraph as the
orchestrator and the typed cross-stage contracts of `pipeline.md §3.3`.

Two design questions were genuinely open and are decided here:

- **Where does the Layer-1 retry budget live, given the Extractor already has
  two internal budgets?** The internal budgets (ADR 0018 malformed-output, ADR
  0021 hallucination/search-before-claim) resolve *before* `extract()` returns —
  the orchestrator never observes them. Layer 1 runs on the *returned*
  AttackSpec, so a Layer-1 failure is a fresh, orchestrator-visible structural
  failure that needs its own retry counter at the orchestration level. Making it
  a third, orchestrator-owned budget keeps each budget single-responsibility and
  keeps the orchestrator the sole router (`architecture.md §1.5`).

- **What carries the structured feedback on a refinement re-run?** `pipeline.md
  §3.3` names a `UserFeedback` object for the human-feedback channel; the
  Extractor's `extract()` takes `blog_content` + `source_summary` strings. Rather
  than change the Extractor's signature (Task 5's locked surface), the
  coordinator appends the structured feedback to the `source_summary`/feedback
  channel as additional prompt context — the same mechanism the Extractor's own
  internal re-prompt uses (`extractor._feedback_for`). `RefinementFeedback`
  carries the typed jury feedback; the orchestrator serializes it into the
  re-run's prompt. No free text crosses a stage boundary untyped: the typed
  object is the contract, the string is its prompt rendering inside one stage.

## Alternatives considered

- **Route Layer-1 failures through the refinement coordinator** — rejected:
  directly violates `validation.md §6.10` ("Any Layer 1 failure → … via the
  stage's *retry* mechanism, **not refinement**"). Non-negotiable.
- **Let the Extractor re-validate itself against Layer 1 internally** — rejected:
  the Validator is a peer framework component (`validation.md §6.1`); folding it
  into the Extractor would make the agent route its own control flow
  (`architecture.md §1.5` violation) and erase the auditable layer boundary.
- **Plain async function pipeline instead of LangGraph** — rejected: the brief
  locks LangGraph, and the explicit node/edge graph makes the routing
  (retry-loop vs refine-loop vs halt vs proceed) auditable, which is the point of
  the deterministic-state-machine discipline (`pipeline.md §3.1`).
- **Change `Extractor.extract()` to take a typed feedback param** — rejected:
  Task 5 locked that surface; threading feedback through the existing prompt
  channel avoids a cross-task signature change while keeping the typed object as
  the contract.

## Consequences

- New module `cyberlab_gen/framework/orchestrator.py` holds `PipelineState`,
  `RefinementFeedback`, `RefinementCoordinator`, `PipelineOutcome`, and
  `build_pipeline` / `run_pipeline`.
- `low_jury_confidence` is a field on the run-report-facing `PipelineOutcome`
  (an `InternalModel`), not on `AttackSpec` — the AttackSpec is the Extractor's
  artifact and must not gain a framework-routing flag (it would leak through the
  `extra="forbid"` round-trip and confuse Layer 1). `pipeline.md §3.2.3` places
  `low_jury_confidence` "in the run report", which is exactly the outcome object.
- `ValidationError` is added to `cyberlab_gen/errors.py` (Layer-validation stage
  error) per the Task-6 brief and ADR 0009's single-hierarchy rule.
- The `--auto` / `--interactive` mode plumbing and the post-Extractor interrupt
  are Task 7's; the orchestrator exposes the seam (an `interactive` flag and the
  outcome carrying everything an interrupt would show) but Phase-1 Task 6 wires
  only `--auto`-equivalent straight-through behavior plus the headless-rejection
  *helper* the CLI will call. The graph itself is mode-agnostic.
