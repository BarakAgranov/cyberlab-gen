# 0106 — Plan-eval failure labels: kill the fabricated `infra_failure`, two honest axes

**Date:** 2026-06-21
**Phase:** 2 (plan-eval hardening; follow-up B from ADR 0105)
**Architecture refs:** ADR 0034 (eval failure scope: `retryable` / `blog_fatal` / `global_fatal`),
ADR 0102 (plan-eval harness — returned-terminal vs raised-failure persistence), ADR 0039 (run-store
persists on every exit path), ADR 0105 (the dead-tool spiral whose `ToolLoopError` surfaced this),
`eval.md §7.4`/`§7.6` (per-blog plan runs).

## Context

The run-20260620 `--stage plan` failure (codebuild run0, a `ToolLoopError` — the ADR-0105 spiral)
surfaced **one event under three different names**, drawn from the *same* `PlanRunRecord` (a raised
`CyberlabGenError` caught in `plan_runner.plan_once`):

| layer | label | honest? |
|---|---|---|
| console (`StderrPlanEvalProgress.blog_run_finished`) | `infra_failure` | **no — fabricated** |
| report `failure_kind` (`PlanRunRecord` / aggregate) | `blog_fatal` | yes (ADR 0034 scope) |
| run.json `status` (run-store) | `failed` | yes (run-store terminal vocabulary) |

`infra_failure` is the misleading one. It exists **only** as a hard-coded console fallback for
`record.status is None` (`progress.py`), and it fired for *every* raised `CyberlabGenError` — both
`blog_fatal` (this blog's content/size: a tool loop, a truncation) and `global_fatal` (systemic:
auth/quota/no-model). Labelling a blog's own tool-loop "infra" is wrong and sends a debugger looking
at credentials/network instead of the spec. The root of the misnomer was the `PlanRunRecord.status`
docstring itself, which framed `status is None` as "an *infra* failure (provider auth/quota/transient)".

These are **not** three names for one fact. There are two genuinely-distinct axes:

1. **`failure_kind`** (eval scope, ADR 0034): `retryable` / `blog_fatal` / `global_fatal` — "should
   the eval run continue to the next blog?" The eval-runner's triage vocabulary.
2. **run-store `status`** (`RunStatus`): `shipped` / `failed` / `interrupted` / `crashed` / the
   route-back/halt statuses — "what terminal state did this single run reach?" A `failed` here is a
   generic, honest terminal status. The cause is preserved as `halt_reason` on disk (ADR 0102).

`infra_failure` was a fabricated **third** vocabulary muddled into the gap between them.

## Decision — canonical taxonomy

- **`failure_kind` is the one honest "kind of failure" label** (`blog_fatal` / `global_fatal` /
  `retryable`). The console now surfaces it: when `status is None`, print `record.failure_kind` (with
  `failed` as a defensive fallback), so **console ↔ report agree**. `infra_failure` is deleted.
- **run-store `status: failed` stays** — it is a different, legitimate axis (the run-store terminal
  status), not a competing name. It is **not** force-merged to `blog_fatal`; conflating the run-store
  vocabulary with the eval-scope vocabulary would itself be dishonest. Traceability to the cause is
  the on-disk `halt_reason` (already persisted, ADR 0102).
- The misleading "infra failure" framing is corrected at its source: the `PlanRunRecord.status`
  docstring and the `plan_runner` comments now say "a *raised* `CyberlabGenError` (no terminal
  status) — not necessarily infra; `failure_kind` carries the honest scope."

### Post-0105 correctness

ADR 0105 (gate + forced terminal emit) makes the dead-tool `ToolLoopError` rare, but the raised path
remains reachable — a model that defies the forced `tool_choice` (`test_tool_loop_error_when_never_finishes`),
`EmitTruncated`, `MalformedOutput`, auth/quota `HardFailure`, etc. The console fix generalises to all
of them: each maps through `classify_pipeline_failure` to its true scope (`blog_fatal` for a post-0105
`ToolLoopError`, `global_fatal` for auth, `retryable` for a persistent transient). So `blog_fatal`
remains the correct label for a post-0105 `ToolLoopError` — the fix is not spiral-specific.

## Scope

Plan-eval only. The Extractor-stage `StderrEvalProgress` line reports `verdict=…`, not `status=`, so
it never had the fabricated label. No change to the run-store `RunStatus` enum, to ADR-0034
classification, or to any routing/abort logic — this is a labelling-honesty fix.

## Consequences

- `progress.py`: the `status is None` console fallback is `record.failure_kind` (→ `failed`), not
  `infra_failure`.
- `plan_metrics.py`: the `PlanRunRecord.status` docstring no longer calls the raised path "infra".
- `plan_runner.py`: the three "infra failure" comments reworded to "a raised `CyberlabGenError` (no
  terminal status); `failure_kind` carries the honest scope".
- Tests: `test_plan_progress_shows_failure_kind_not_fabricated_infra_failure` pins `status=blog_fatal`
  on the console for a raised blog-fatal failure and asserts `infra_failure` is gone. `just verify`
  green.
- A reader now sees the same scope on the console and in the report; the run.json `status: failed`
  remains the run-store axis, with the cause in `halt_reason`.
