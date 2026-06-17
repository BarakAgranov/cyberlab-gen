# 0093 — The plan-refinement coordinator (second orchestration pipeline) + the Planner-Jury

**Date:** 2026-06-17
**Phase:** 2 (Task 4 — Planner-Jury + refinement extension + route-back)
**Architecture refs:** `pipeline.md §3.2.6`–`§3.2.7` (the Planner + Planner-Jury stages), `§3.1`
(deterministic state machine; the orchestrator routes, agents never do), `architecture.md §1.5`
(the LLM produces content / judgments / a structured refusal; the framework maps them to control
flow), `§1.7` (retry vs refinement), `agents.md §5.8` (the Planner-Jury contract). Builds on ADR
0092 (the Planner's `PlanAttempt` outcome), ADR 0054/0091 (targeted-patch refinement), ADR 0056
(iteration caps), ADR 0067 (the rubric-floor backstop), ADR 0078 (the verify-only jury). Fork B,
architect-approved.

## Context

Task 4 must demonstrate, on the slice, the three plan-stage control-flow mechanisms (`§5.6`/`§5.8`):
the Planner-Jury `revise` targeted-patch loop, the Planner-failure **route-back to the Extractor**,
and the disagreement-without-progress ship/halt. These are graph behaviours, so a coordinator is
required here; Task 6 "wires the linear graph … with the refinement/route-back edges from Task 4"
(the verb, persistence, Layer 2, and the cross-pipeline route-back wiring are Task 6's).

## Decision

**A second LangGraph coordinator, `framework/plan_orchestrator.py`**, mirroring the extract
orchestrator's structure but lean: `Planner → Planner-Jury`, linear (no `Stage`/`Node` abstraction —
that lands at the first *parallel* node, the Phase-3 Generators; `dev/phase-2-seams.md` ③.1). Nodes
set a node-decided `route` on the typed `PlanPipelineState`; pure conditional edges read it.

- **`revise` → targeted-patch refine.** A `revise` re-runs `Planner.refine` (ADR 0054/0091, generic
  over `SpecEnvelope`) on only the flagged manifest fields, bounded by the per-agent `refinement_cap`
  (Phase-1 placeholder 3). Exhaustion: `revise` → ship `PLANNED_LOW_CONFIDENCE` with the unresolved
  feedback; `reject` → `HALTED_REJECT`.
- **Route-back as a terminal *outcome*, not a raise.** When the Planner emits
  `attackspec_incoherent` (ADR 0092), the coordinator terminates `ROUTE_BACK_TO_EXTRACTOR` and
  carries the structured `refusal`. The jury is never reached and `Planner.refine` is never called —
  the Planner flagged a defect it may not repair (`agents.md §5.7`). Task 6 connects this edge to a
  real Extractor re-run; here the route-back **decision** is the asserted exit criterion.
- **`cannot_plan` → `HALTED_CANNOT_PLAN`** with the gap report.
- **Sub-floor-`approve` backstop** (ADR-0067 mirror): an `approve` whose dimension scores fall below
  the floor is refused `HALTED_JURY_INCONSISTENT` — mechanical safety, framework-owned (`§1.6`).
- **Global iteration cap** (ADR 0056) bounds a runaway; `recursion_limit` is the graph-level backstop.

**Every terminal state is *returned* as a `PlanPipelineOutcome` (never raised)** — including the
halts. Route-back MUST be a returned value (it is a routing decision, not an error), and returning
all terminal states uniformly lets the Task-6 `plan` verb own the halt-vs-route-back-vs-ship policy.
This deliberately diverges from the extract `run_pipeline` (which raises on halts): the extract
contract predates this, and the plan pipeline has no caller yet to constrain it.

**Share what's shareable, don't copy the closure.** The genuinely-shared graph mechanics — the
stage-span node wrappers — move to `framework/graph_support.py` (`traced_async` / `traced_sync`,
state-generic), used by **both** coordinators; the extract orchestrator's private `_traced_*` are
deleted in favour of them. Everything else (state, status, nodes, caps) is coordinator-specific; the
`Stage`/`Node` refactor that will consolidate the two is deferred to the first parallel node, per the
Fork-B guidance — sharing only the small, safe mechanics now keeps that later refactor clean without
a ~390-line copy-paste.

**The Planner-Jury** (`agents/planner_jury/`) subclasses `ToolUsingAgent` with `verify_only_tools=True`
(ADR 0078: the read/write split enforced by tool availability — `external_lookup` only, no
`propose_*`; inherited via the base default, exactly like the Extractor-Jury). It **reuses
`JuryVerdict`** rather than a bespoke verdict pair — `agents.md §5.8` / `pipeline.md §3.2.7` both say
"same shape as Extractor-Jury", and the four rubric dimensions read naturally for the manifest
(fidelity to the AttackSpec, coverage completeness, correctness of the Planner's `llm_inference`
provenance, structural validity). Its rubric floor is its **own** 0.7 placeholder constant (so the
architect calibrates the two juries independently per `CALIBRATION.md`); Task 4 builds the asymmetric
discipline (tune up on false-approval, never down on false-rejection), not the number.

## Consequences

- New `framework/plan_orchestrator.py` (`build_plan_pipeline`, `run_plan_pipeline`,
  `finalize_plan_outcome`, `PlanPipelineState`/`Status`/`Outcome`); new `framework/graph_support.py`;
  extract orchestrator refactored onto the shared wrappers (its `_traced_*` deleted, `Callable`
  import dropped). New `agents/planner_jury/` (`PlannerJury` + prompt). Re-exports added to
  `framework`/`agents` surfaces.
- Tests: the four exit-criterion paths (route-back asserted — jury never reached, no manifest, no
  refine; revise→refine→re-review converges; exhausted-revise low-confidence; reject halt) plus
  cannot_plan, sub-floor-approve, the global cap, and the driver outcome mapping. Planner-Jury: each
  verdict fires, verify-only wiring, rubric-floor placeholder + validation. Full suite green (844
  passed / 1 skipped).
- **No architecture-doc change** — `pipeline.md §3.2.6`–`§3.2.7` / `agents.md §5.8` already specify
  these stages and outcomes; this records the *mechanism* (the coordinator, the shared helpers, the
  return-everything contract, the JuryVerdict reuse).

## Alternatives considered

- **Extend the extract orchestrator's `build_pipeline` with plan nodes.** Rejected: the extract graph
  is extract-shaped (extract/validate/grounding/enrich/jury); a second clean coordinator is clearer
  than overloading it, and the real consolidation is the deferred `Stage`/`Node` refactor.
- **A bespoke `ManifestJuryVerdict`.** Rejected: the docs say "same shape as Extractor-Jury";
  inventing a parallel verdict pair adds a contract with no behavioural need.
- **Raise on plan halts (mirror `run_pipeline`).** Rejected: route-back is not an error and must be a
  returned value; returning all terminal states uniformly is cleaner and defers the CLI policy to
  Task 6.
