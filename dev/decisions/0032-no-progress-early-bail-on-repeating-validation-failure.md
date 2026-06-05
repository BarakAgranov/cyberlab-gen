# 0032 — No-progress early-bail on a repeating identical validation failure

**Date:** 2026-06-05
**Phase:** 1 (provider-backed eval hardening; "stop paying to retry a failure that isn't converging")
**Architecture refs:** `provider-interface.md §6.2` (malformed-output retry), `architecture.md §1.7` (retry vs refinement), ADR 0018 (two-layer structural-retry budget — **refined**), ADR 0030 (eval spend guards — **amended**), ADR 0031 (invalid-emit fallback)

## Context

A real provider-backed run had the Extractor judge a blog `in_scope` but emit an
`AttackSpec` with no `chain`, which the schema rejects with the model-validator
error `chain is required when in_scope` (`attack_spec.py:_scope_consistency`).
Because that error is a `mode="after"` validator, *every other field had already
validated* — the emit was a complete, well-formed spec that simply omitted the
chain. The model reproduced the identical error on every re-prompt.

The retry machinery paid for that with no chance of progress, at two layers:

1. **Provider** `_extract_structured` re-prompts `MALFORMED_OUTPUT_RETRIES.max_attempts`
   (2) times, then raises `MalformedOutput`.
2. **Call surface** `_with_structural_retry` retries the whole stage
   `1 + DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` (3) times, then raises `AgentFailure`.

So one `run_once` burned ~6–9 full long-context Extractor calls (the entire blog
re-sent each time) on the *identical* failure, ~$4 across runs.

Two facts the diagnosis corrected:

- `AgentFailure` subclasses `CyberlabGenError` **directly** (not `TransientFailure`),
  so the eval runner *does* tag it `non_retryable` (`runner.py:189-194`). The
  failure was not mis-classified as retryable. The eval-level fail-fast (ADR 0030)
  is simply too coarse — it acts at *run* granularity and cannot stop the
  within-run retry storm, which is where the money goes.
- `_normalize_failure` (ADR 0030) stripped `toolu_…`, `messages.N`, and digit runs
  but **not** the alphanumeric `request_id` (`req_…`). The `gen0-20260602` archive
  proves the consequence: six `non_retryable` 400s that differed *only* by
  `request_id` ran in full because each normalized signature was distinct, so
  fail-fast never reached two-in-a-row.

## Decision

Add a **no-progress early-bail** at both retry layers, and close the
normalization gap:

1. **Provider `_extract_structured`** takes a new `prior_parse_error` and tracks
   the previous attempt's error. If an attempt reproduces the *identical* error
   (including the one that triggered a forced extract from `complete_with_tools`),
   it bails immediately instead of spending the rest of the budget. The
   triggering emit error is threaded in from the `complete_with_tools` fallback.
   The raised `MalformedOutput` message is made **deterministic for a given error**
   (the varying "after N attempt(s)" count is dropped) so the call surface can
   recognise the same failure across stage attempts.

2. **Call surface `_with_structural_retry`** compares each `MalformedOutput`'s
   `str(exc)` to the previous attempt's; an identical repeat aborts the stage
   early (raising `AgentFailure` as before). A *different* error still consumes the
   full budget — a changing error may signal convergence.

3. **Eval `_normalize_failure`** also collapses `req_[A-Za-z0-9]+` → `req_X` so
   `request_id`-only variation no longer defeats fail-fast.

4. **Content-failure diagnostic** — `_dump_emit_on_validation_error`, two outputs
   by audience:
   - **Always-on verdict** (no env var): a concise `WARNING` naming the
     load-bearing distinction — *truncated* (`stop_reason == "max_tokens"`, the
     authoritative signal, corroborated by `output_tokens` vs `max_tokens`) vs
     *complete-but-schema-invalid* (a genuine content problem). The response's
     `stop_reason` and per-call `output_tokens` are threaded in from both emit
     parse sites. This is what a maintainer needs and it surfaces on every run via
     Python's last-resort stderr handler (the eval CLI configures no logging).
   - **Opt-in content dump** (`CYBERLAB_GEN_DEBUG_EMIT`, off by default): the raw
     emitted arguments to stderr. Unlike the tool-loop dump (ids only, ADR 0031)
     this prints content — the point is to see *what* the model produced (the
     chainless `AttackSpec`). Large, so gated.

   **Revision note:** the diagnostic initially gated *everything* behind the env
   var, so a run without the var set produced no verdict — the user saw only the
   pre-existing parse-error warning and could not tell truncation from omission.
   The always-on verdict (keyed on `stop_reason`) fixes that. New evidence (the
   missing required field *alternates* across runs — `extraction_metadata`, a
   late-declared field, then `chain`) is consistent with `max_tokens=4096`
   truncation and weakens the initial "deliberate omission" read; the
   `stop_reason` verdict settles it on the next run without paying to re-parse a
   dump.

The bail is **never progress over a cap** — it only ever stops *earlier* than the
existing budget; it never raises the ceiling, never routes to refinement, and
never decides whether output ships. It is deterministic framework code
(`architecture.md §1.5/§1.7`).

## Alternatives considered

- **Only fix the eval fail-fast guard** — rejected: the dominant cost is *within*
  a single run; a run-granularity guard still pays ~2 full doomed runs (~$4)
  before aborting.
- **Lower the budgets globally** — rejected: a smaller budget hurts the genuine
  converging case (a *different* error each attempt) as much as the doomed one.
  The no-progress signal distinguishes them; a blunt cap cannot.
- **LLM-judge "is the model making progress?"** — rejected outright: retry-budget
  control flow is framework, never LLM (`architecture.md §1.5/§1.6`). Exact
  string equality of the validation error is the mechanical signal.
- **Fix Symptom 2 (the missing chain) now** — deferred: the *mechanism* is certain
  (a complete `in_scope` spec with `chain` omitted) but the model's *motivation*
  cannot be confirmed without live spend. The diagnostic captures real data first;
  the prompt/`max_tokens` change follows from it.

## Follow-up: Extractor output-token budget (the Symptom-2 fix)

The diagnostic confirmed the missing-field error **alternates** across runs
(`extraction_metadata`, a field declared late in `AttackSpec`, then `chain`) —
the signature of a **truncated emit**, not deliberate omission. Root cause: the
Extractor called `run_with_tools` with **no `max_tokens`**, falling to the
provider default `DEFAULT_MAX_TOKENS = 4096`, despite the adapter docstring saying
the Extractor should pass an explicit value. 4096 truncates a full AttackSpec
mid-emit.

Fix: `Extractor` now passes `DEFAULT_EXTRACTOR_MAX_TOKENS = 16384` (configurable
via a `max_output_tokens` constructor arg).

**Why 16384, and the ceiling that bounds it.** The model ceiling for
`claude-opus-4-8` is **128,000** output tokens (1M context). But the provider call
is **non-streaming**, and the Anthropic SDK
(`_base_client._calculate_nonstreaming_timeout`) raises `ValueError` once the
estimated generation time exceeds 10 minutes — i.e. `max_tokens > 600/3600 ×
128_000 ≈ 21_333`. So the practical hard cap on the current path is **21,333**,
not 128K. 16384 is 4× the old default, sits below 21,333 with margin under the
10-minute wall, and covers a realistically rich spec (a measured 9-step Sysdig
spec serialises to ~12K output tokens). Reaching toward the true 128K ceiling
requires converting the tool loop to **streaming**.

**Unhandled gap (surfaced, not fixed).** `chain_steps` has no schema maximum, so a
sufficiently long blog produces an AttackSpec that exceeds *any* fixed
`max_tokens` and truncates with **no recourse** — there is no chunked/continuation
emit, no long-blog split anywhere in the package (confirmed by search).
`implementation-plan.md §4.6` flags long-blog handling only as a *risk to watch*
("if chunking doesn't work cleanly, that's a Phase 1 finding worth surfacing
now"), never a built mechanism. With the no-progress bail + raised budget, such a
case now fails fast and cheap rather than burning the retry storm — but it still
fails. A streaming + chunked-emit path is the real fix and remains open.

## Consequences

- `AnthropicProvider._extract_structured` gains `prior_parse_error`; its
  `MalformedOutput` message no longer embeds the attempt count (no caller matched
  on that text). New `_dump_emit_on_validation_error` + `_DEBUG_EMIT_ENV`; `json`
  import added.
- `AgentRunner._with_structural_retry` bails on an identical-repeat `MalformedOutput`.
  The existing exhaustion test stays green because its double raises a *varying*
  message; a new test covers the identical-repeat bail.
- `eval/runner/runner.py::_normalize_failure` collapses `req_…`. Existing
  normalization test unaffected (no request_id); new tests cover the request_id
  case end-to-end.
- Worst-case within-run Extractor calls for a stuck content failure drop from
  ~9 to ~4; combined with the now-effective fail-fast, a systemically doomed blog
  aborts after 2 runs and archives the partial (ADR 0028/0030 path, unchanged).
- **Latent issue flagged, not fixed here:** the Extractor calls `run_with_tools`
  without `max_tokens`, so the `AttackSpec` emit is capped at the provider default
  (`DEFAULT_MAX_TOKENS=4096`) despite the adapter docstring saying the Extractor
  should pass more. Not the cause of the `chain is required` error (truncation
  would drop fields declared *after* `chain` and surface field-level errors, not
  the after-validator), but a real truncation risk. Tracked for the Symptom-2
  follow-up once the diagnostic confirms the emitted content.
