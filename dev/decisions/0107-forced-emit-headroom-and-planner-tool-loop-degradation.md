# 0107 — Forced-emit retry headroom for producers + graceful Planner tool-loop degradation

**Date:** 2026-06-21
**Phase:** 2 (plan-eval hardening; follow-up to ADR 0105)
**Architecture refs:** ADR 0105 (the forced terminal emit — part 3 is amended here), ADR 0106 (the
plan-eval failure-label reconciliation that made this visible), `architecture.md §1.5`/`§1.6` (the
LLM/framework split — tool-loop control + terminal-status decisions are framework, deterministic),
`provider-interface.md §4` (the tool loop), `coding-conventions.md §5.5` (descriptive names, no
ordinal tokens).

## Context — the run-20260621 codebuild Planner spiral

The architect's second provider-backed `--stage plan` run (20260621T154616Z) ran codebuild 3× and got
three outcomes from one input: run 1 shipped, **run 2 `blog_fatal`**, run 3 `route_back`. A 4-agent
adversarial investigation (Phoenix trace 50ccb490…, the step-13 payload re-validated offline)
root-caused run 2 **decisively**:

- Run 2 was the **Planner** (a producer; `DEFAULT_MAX_TOOL_ITERATIONS = 12` → `request_limit 13`),
  not the verify-only jury (limit 9) ADR 0105 fixed. The jury never ran.
- The Planner spent **all 12 tool turns** on tools (`query_value_types_registry`×9, `propose_facet`×4,
  `external_lookup`×3) and made its **first** emit attempt only when **forced** on step 13.
- ADR-0105 part 3 **fired correctly**: step 13 carried `tool_choice=[final_result]` and the model
  emitted a complete, well-formed PlanAttempt (110 s, 11.4k output tokens). **But the emit failed
  validation** — 12 identical errors `"confidence is required when source is llm_inference"`
  (`provenance.py:89-90`): the model set `source=llm_inference` on 12 provenance fields and omitted
  the required `confidence`.
- pydantic-ai (holding `output_retries=2`, unused — no prior emit) scheduled an output-retry =
  request 14; `request_limit=13` is checked **before** that call → `UsageLimitExceeded` →
  `ToolLoopError` → run-store `failed`, eval `blog_fatal`. **$3.08 burned for nothing.**

**The design gap:** ADR-0105 part 3 guarantees the loop *emits* on the last permitted request — it
does **not** guarantee that emit *validates*. The force fires at `run_step >= request_limit` (the very
last turn), so **any** validation failure on the forced emit needs a retry the budget forbids. The two
budgets (`request_limit`, `output_retries`) were never reconciled. The single-shot force was validated
against the tiny `JuryVerdict` (valid first-shot); transplanted onto a large, validator-heavy producer
output (`PlanAttempt` wrapping a deeply-nested `LabManifest`), it re-opened the very zero-output death
it was meant to close — one validation failure removed.

A second defect compounded it: `plan_node` calls `planner.plan()`/`refine()` with **no try/except**, so
a `ToolLoopError` escapes the orchestrator to the eval/CLI boundary as a raw exception (`blog_fatal`),
strictly worse than run 3's clean `route_back` on identical input.

(Run 3's `route_back` is **correct** behaviour — the Planner detected the missing `value_type` for the
central flowing value and routed back, per `agents.md §5.7`. Run 1's ship is the **unwired manifest
registry-membership check** biting as documented (ADR 0099 §6). Both are tracked separately; neither is
this ADR's concern.)

## Decision — two complementary framework fixes (both deterministic; no LLM control)

1. **Reserve forced-emit retry headroom** (`output_forcing_model_settings`, `anthropic_provider.py`).
   The force now fires at `run_step >= max(1, request_limit - emit_headroom)` where
   `emit_headroom = output_retries` (the within-call output-validation re-prompt budget the provider
   already holds, `DEFAULT_OUTPUT_RETRIES = 2`). So a forced-but-**invalid** emit lands with its
   `output_retries` re-prompts still **inside** the request budget: the validation-repair loop can fix
   a mechanical content error (the `confidence` omission) and still ship within budget. This preserves
   ADR-0105's always-emit guarantee and adds the missing validate-within-budget guarantee. It
   generalises to **all** tool agents (the jury too — harmless there, its output is valid first-shot).
   **Accepted tradeoff:** the model loses `output_retries` (2) of its *free* tool turns before the
   force engages (the Planner: 12 → 10 free turns). We do **not** speculatively bump
   `DEFAULT_MAX_TOOL_ITERATIONS` to compensate — whether the Planner genuinely needs all 12 free turns
   is an open calibration question for the architect's paid re-run, not a number to guess here.

2. **Graceful Planner tool-loop degradation** (`plan_node`, `plan_orchestrator.py`). `plan_node` now
   catches `ToolLoopError` around the Planner `plan()`/`refine()` calls and converts it to a new
   deterministic terminal status **`HALTED_PLANNER_EMIT_EXHAUSTED`** (+ a halt_reason), instead of
   letting the raw exception escape. The Planner produced nothing to route on, so the framework halts
   cleanly — exactly the kind of terminal-status decision the orchestrator already owns (`§1.5`); a
   fixed `except` clause, no LLM judgment, no fabricated Planner content. Mapped to `RunStatus.FAILED`
   in `PLAN_STATUS_TO_RUN_STATUS` (it *is* a failed run — no manifest), but now it surfaces as a
   named, honest terminal (`status=halted_planner_emit_exhausted`) rather than a generic `blog_fatal`,
   and the eval records it as a returned terminal (no `failure_kind`) instead of a raised failure.
   Scope: `plan_node` only (the Planner producer); the jury path is out of scope (ADR 0105 + fix 1
   make a jury `ToolLoopError` very unlikely).

The new status name is descriptive with no ordinal token, per `coding-conventions.md §5.5`.

## Out of scope (deliberately untouched / separate items)

- `DEFAULT_MAX_TOOL_ITERATIONS` (a calibration value — the architect's, post paid re-run).
- The `confidence`-omission content error itself (a Planner prompt / schema concern — proximate
  trigger; with fix 1 the retry now repairs it, but eliminating it is a separate P1).
- The missing `github_actor_id` `value_type` (Extractor/fixture defect — the variance driver).
- Wiring the manifest registry-membership check (the owned ADR-0099 §6 deferral — the run-1
  false-approve defence).
- The eval-harness `layer2_*` → `semantic_cross_check_*` metric rename (a separate naming-discipline
  follow-up, ADR-0046 precedent).

## Consequences

- A forced terminal emit that fails validation on the last turn now gets its output-retries within
  budget; a single recurring mechanical content error no longer converts a valid-JSON emit into a
  `ToolLoopError`. Producers with large outputs are covered, not just small verify-only outputs.
- A Planner that still cannot emit a valid manifest within budget degrades to a named
  `HALTED_PLANNER_EMIT_EXHAUSTED` terminal (run-store `failed`) instead of a raw `blog_fatal`.
- New enum member `PlanPipelineStatus.HALTED_PLANNER_EMIT_EXHAUSTED` + its `PLAN_STATUS_TO_RUN_STATUS`
  mapping; importers/matches that enumerate the status set are updated.
- Tests: forced-emit headroom (the threshold reserves `output_retries`; forced-but-invalid emit
  re-prompts and ships within budget); `plan_node` converts a `ToolLoopError` to the new terminal
  status. `just verify` green.
- ADR-0105 part 3 is **amended**: its single-shot force is recorded as unsafe for large producer
  outputs without reserved headroom; this ADR supplies the headroom.
