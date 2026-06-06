# 0043 — Proposal-tool rejections are advisory, never fatal

**Date:** 2026-06-07
**Phase:** 1 (operational hardening — sibling of the ADR 0042 blocker)
**Architecture refs:** `schema.md §4.16` (proposal authority — the Extractor proposes
only `target:*` / blog-derived `lab_class_signal:*` facets), `agents.md §5.4` (the
Extractor's three tools). Directly follows ADR 0042, which fixed the same fatal
mechanism for `external_lookup` and **flagged this case for sign-off**.

## Context

ADR 0042 found that the provider bridge turns any `is_error=True` tool result into a
pydantic-ai `ModelRetry`, and the tool-retry budget is **1**, so a tool error the model
repeats escalates to a fatal `ToolRetryError` that kills the whole extraction. The 0042
audit found the same latent mechanism in the Extractor's *proposal* tools, which returned
`is_error=True` for:

- `propose_value_type` — invalid proposal args;
- `propose_facet` — a category outside the Extractor's authority (e.g. `runtime:*`);
- `propose_facet` — invalid proposal args.

A registry proposal is an **optional advisory side-channel** (recorded for jury/user
review; it is *not* part of the `AttackSpec` the run must emit). So a rejected proposal
killing the core extraction is clearly wrong — and the `runtime:*` wrong-category case is
especially analogous to the lookup bug, since retrying the same category can never
succeed. The maintainer signed off on fixing it after the 0042 audit.

## Decision

The three proposal-rejection sites now return **`is_error=False`** with content that
plainly states the proposal was **rejected / not recorded** and the model should continue
(it may re-propose a corrected one, or simply move on). The proposal is still **not added**
to the collected `value_type_proposals` / `facet_proposals` — only the *fatality* changes,
not the authority gate or what gets recorded. The model still sees the rejection reason as
a normal tool result, so it can self-correct without a budgeted, fatal retry.

After this, the only `is_error=True` results the Extractor executor still returns are:

- `external_lookup` against `nvd` with no `cve_id` — a genuinely fixable usage error
  (the model can add the id); retrying is the right response, and it is low-risk.
- the defensive "unknown tool" branch in `execute()` — unreachable in practice (pydantic-ai
  only dispatches advertised tools).

The provider bridge (`_make_tool`'s `is_error → ModelRetry`) is left generic and correct
for genuinely-fixable errors; the policy lives in the executor, which knows an optional
advisory rejection from a fixable-args error.

## Consequences

- `ExtractorToolExecutor._propose_value_type` / `_propose_facet` no longer return
  `is_error=True`; an optional proposal can never block the core emit.
- `test_propose_runtime_facet_rejected_at_boundary` now asserts the graceful (non-error,
  not-recorded) behavior; the authority gate and "not recorded" outcome are unchanged.
- Behavioral change: a model that emits a malformed or out-of-authority proposal no longer
  risks a fatal `ToolRetryError`; the proposal is simply dropped with an explanation.
