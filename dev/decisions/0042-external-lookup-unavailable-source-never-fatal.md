# 0042 — An unavailable external_lookup source must never be fatal

**Date:** 2026-06-07
**Phase:** 1 (operational hardening — real-run blocker)
**Architecture refs:** `agents.md §5.4` (Extractor tool inventory; the Extractor is a
read-only enrichment-assisted agent), `schema.md §4.15` (search-before-claim),
`pipeline.md §3.2.4` (enrichment graceful-degradation: an unavailable external source
is recorded and skipped, never fatal). Builds on ADR 0021 (Extractor tools), ADR 0029
(answer-every-tool-call), ADR 0036 (pydantic-ai migration — the change that turned a
tool error into a bounded, fatal `ModelRetry`).

## Context

A real `extract` of the Wiz CodeBuild blog died with `$0.478` spent and nothing
produced. Evidence (Phoenix trace + persisted run + code):

1. The model called `external_lookup(source_id='mitre', ...)` — a *reasonable*
   search-before-claim attempt (MITRE ATT&CK is a real authoritative source the blog
   references, and the tool's own description says a lookup is "Required before claiming
   any external_api-sourced value"). It is **not** a hallucination.
2. The `external_data_sources` registry contains **only `nvd`** (verified). So `'mitre'`
   resolved to "unknown", and `ExtractorToolExecutor._external_lookup` returned a
   `ToolResult(is_error=True, "unknown external source id 'mitre'")`.
3. The provider bridge `anthropic_provider._make_tool` converts **any** `is_error=True`
   tool result into `raise ModelRetry(...)`.
4. pydantic-ai's **tool**-retry budget is **1** here: the adapter passes
   `retries={'output': 2}`, and pydantic-ai's `_normalize_agent_retries(..., default=1)`
   leaves the `tools` budget at the default `1`. So: initial call → `ModelRetry`; one
   retry → the model repeats the identical (unservable) call → `ModelRetry` again →
   budget exhausted → `ToolRetryError` (an `UnexpectedModelBehavior`). **This exactly
   matches the trace's two LLM calls.**
5. `_invoke` catches `UnexpectedModelBehavior` and `_map_error` falls through to
   `MalformedOutput`, failing the run. The 840/1012 output tokens were the two
   tool-call turns, **not** a partial AttackSpec emit (so this was never truncation,
   and there was no partial spec to persist).

Root cause (evidence-backed, not hypothesis): **retrying is the wrong response to an
unavailable source.** `ModelRetry` means "you did something malformed, try again";
retrying an identical lookup against a source that cannot be served is guaranteed to
fail, and the bounded budget makes that guaranteed-failure fatal. The bug is that an
*availability* condition was reported as a retryable *error*. Notably, the executor
**already** handles the adjacent cases gracefully (registered-but-unwired source,
nvd-with-no-client, nvd rate-limited all return `is_error=False`, recorded
`found=False`); only the "unknown source id" branch was inconsistent — and any source
other than `nvd` hits the fatal class (the registry even tells the model to cross-check
MSRC, which isn't registered either).

## Decision

`ExtractorToolExecutor._external_lookup` now treats **every** non-NVD source —
registered-but-unwired *or* unknown — as **unavailable**: it records a `found=False`
`ExternalLookupRecord` and returns a non-error `ToolResult` whose content tells the
model to treat the value as requiring external research (set the field to
`unknown_from_blog` with a reason) and continue. The `external_source(...)` registry
check now only shapes the *message* ("registered but not integrated" vs "not a known
source"), never the error flag. This covers the whole source class (nvd, mitre, msrc,
cisa, any id) in one place, not just `'mitre'`.

Net effect: an unavailable lookup no longer raises `ModelRetry`, so it can never exhaust
the tool-retry budget or escalate to a fatal `ToolRetryError`. The run flows into the
**normal** framework path — the Extractor's existing feedback already instructs the
model to mark ungroundable values `unknown_from_blog` — so the extraction can complete.
A doomed run no longer burns money on retries it cannot win.

## Scope: what this changes and what it deliberately does NOT

- **In scope:** `external_lookup` source *availability*. Unavailable → graceful,
  recorded, non-fatal.
- **NOT in scope (separate follow-up, explicitly):** wiring real NVD/MITRE data. An
  unavailable source still produces a `found=False` record; grounding it is future work.
- **NOT changed here (flagged for a decision):** the other `is_error=True` sites
  (`propose_value_type` / `propose_facet` invalid-or-unauthorized args, `external_lookup`
  nvd-missing-`cve_id`). These share the same `is_error → ModelRetry →` (budget 1) `→
  fatal` mechanism, so a stubborn model could in principle kill an extraction over an
  *optional* proposal. They differ from the lookup case in that retrying *can* fix a
  malformed-args error, and they did not reliably trigger here. Whether to also make the
  optional-proposal tools non-fatal (proposals are an advisory side-channel and arguably
  should never block the core emit) is a semantic judgment recorded for maintainer
  sign-off, not made in this ADR.
- **The provider bridge is left generic.** `_make_tool`'s `is_error → ModelRetry`
  mapping is correct for genuinely malformed-args errors and is shared by all agents;
  the fix belongs in the executor (which knows an availability condition from a
  fixable-args error), not in the provider.

## Consequences

- `ExtractorToolExecutor._external_lookup` no longer returns `is_error=True` for any
  source-availability condition; the unknown-source unit test now asserts the graceful
  (non-error, `found=False`) behavior.
- No provider/orchestrator/Protocol surface changed. `MockProvider`, the call surface,
  and the jury's independent search-before-claim verification (which keys CVE checks on
  `nvd` records) are unaffected.
- Behavioral change: a real extraction referencing a non-NVD external source completes
  (marking the value as requiring research) instead of dying with a fatal
  `ToolRetryError`.
