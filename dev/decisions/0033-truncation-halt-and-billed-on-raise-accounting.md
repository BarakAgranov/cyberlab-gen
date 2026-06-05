# 0033 — Truncation halt (non-retryable emit) + billed-usage-on-raise accounting

**Date:** 2026-06-05
**Phase:** 1 (provider-backed eval hardening; "stop a truncating run from burning the whole budget")
**Architecture refs:** `provider-interface.md §6.2` (malformed-output retry), `provider-interface.md §5` (cost tracking; framework owns budget decisions), `architecture.md §1.5/§1.7` (retry is framework control flow, never LLM), ADR 0018 (two-layer structural-retry budget — **amended**), ADR 0030 (eval spend guards / real per-run cost — **amended**), ADR 0032 (no-progress early-bail + truncation diagnostic — **builds on**)

## Context

A single provider-backed run cost $5.12 and *still shipped a truncated spec*. ADR
0032 diagnosed the dominant driver: when the Extractor's emitted `AttackSpec`
exceeds `max_tokens` (16384), the vendor returns `stop_reason == "max_tokens"` and
a truncated, schema-invalid emit. The retry machinery then regenerates a full
~16K-token output (Opus output is $25/M) that truncates **again**, across two
layers — the provider's internal malformed-retry loop (`_extract_structured`, 2
attempts) and the call surface's structural-retry loop (`_with_structural_retry`,
3 attempts) — plus the Extractor's own hallucination-retry loop on top.

ADR 0032's no-progress bail does **not** catch this: it only short-circuits when
the *same* validation error repeats, but truncation cuts the output at a different
point each regeneration, so the parse error *varies* (`extraction_metadata`
missing one attempt, `chain` missing the next). The bail sees "different errors,"
never fires, and burns the full budget. The single most expensive failure mode ran
nearly unbounded.

A second, related defect made the cost untrustworthy: `CostRecordingProvider._record`
(ADR 0030) recorded a call's cost only on **successful** return. A
`complete_with_tools` that ultimately *raises* (a truncated/malformed emit that
exhausted its retries) was billed by Anthropic but never recorded — so real spend
exceeded the report and the cost cap couldn't see it.

## Decision

### 1. Truncation is a fast, non-retryable halt (`EmitTruncated`)

A new error `EmitTruncated(MalformedOutput)` is raised the moment an emit fails
schema validation **and** `stop_reason == "max_tokens"` (the authoritative
truncation signal the ADR 0032 diagnostic already captures). It is raised at both
emit-parse sites in the adapter:

- `complete_with_tools` finish-turn emit (replaces the forced-extract fallback when
  truncated);
- `_extract_structured` forced-emit attempt (raised before the no-progress bail —
  truncation is known on the *first* attempt; no-progress needs two).

`EmitTruncated` **subclasses** `MalformedOutput` because it *is* a malformed parse
— but it is **never retried**. The exception *type* encodes retryability:

- The provider raises it immediately instead of spending its malformed-output
  budget.
- The call surface `_with_structural_retry` catches `EmitTruncated` *before* the
  `MalformedOutput` handler and **re-raises** it, bypassing the structural-retry
  budget entirely. It therefore is **not** wrapped in `AgentFailure`.
- It is not a `MalformedOutput` worth re-prompting, so it short-circuits the
  Extractor's hallucination-retry loop too (that loop never catches it).

One emit-cost worth of vendor spend, then halt — versus ~6–9 doomed 16K-token
regenerations before.

**Honest `halt_reason`.** The message names the condition and the only remedies
that help — *raise `max_tokens` (up to the non-streaming ceiling) or shorten the
input*; retrying cannot. Because the eval/CLI surface uses `str(exc)` as the
`halt_reason`/user error, a truncated run now reads e.g.:

> the AttackSpec emit was truncated at the 16384-token output limit
> (stop_reason=max_tokens, output_tokens=16384): the structured output was cut off
> mid-emit and cannot validate against the schema. Retrying regenerates the same
> oversized output and truncates again at the same budget — raise max_tokens (up to
> the non-streaming ceiling) or shorten the input.

The eval runner tags it `non_retryable` (it is a `CyberlabGenError`, not a
`TransientFailure`), and its deterministic message (digits normalized by ADR
0032's `_normalize_failure`) lets fail-fast abort a systemically-truncating blog
after 2 runs — cheap, not a $5 burn.

### 2. Billed usage is attached to a raised `ProviderError`

`ProviderError` gains optional `usage: TokenUsage | None` and `model: str | None`.
A new adapter helper `_with_usage(exc, model=, acc=)` finalizes the invocation's
accumulated `_UsageAccumulator` onto the error before it is raised, at every
post-billing raise site (`MalformedOutput`, `EmitTruncated`, `ToolLoopError`,
`TransientFailure`, `HardFailure`). Best-effort: skips when nothing was billed or
usage is already attached, and swallows a finalize failure (missing pricing) so
accounting never masks the original error.

`CostRecordingProvider` now wraps each delegated call; on a `ProviderError` it
records the attached billed usage as a `CallOutcome.FAILED` entry (then re-raises)
before the error propagates. So `ledger.total_usd` — and the cost cap built on it —
count billed-but-raised spend that was previously invisible.

This is deterministic framework code: the bail only ever halts *earlier* than the
existing budget; it never raises the ceiling, never routes to refinement, never
decides whether output ships (`architecture.md §1.5/§1.7`). The cost cap decision
stays with the framework, not the provider (`provider-interface.md §5.3`).

## Alternatives considered

- **`EmitTruncated` as a sibling of `MalformedOutput` (both under `ProviderError`)**
  — would propagate past the call surface's `except MalformedOutput` *implicitly*,
  no code change. Rejected: a truncated emit genuinely *is* a malformed parse, so
  the `is-a` is honest; and an *explicit* re-raise in the call surface documents
  the non-retryable contract better than relying on a reader noticing the type is
  not a `MalformedOutput`.
- **Detect truncation by `output_tokens >= max_tokens` (not `stop_reason`)** —
  rejected: `stop_reason == "max_tokens"` is authoritative and exact; the
  token-count comparison is a corroborating heuristic (kept only in the
  diagnostic) that could mis-halt a complete-but-invalid emit that happens to land
  near the limit.
- **Lower `max_tokens` or the retry budgets globally** — rejected (same reasoning
  as ADR 0032): hurts the genuinely-converging case as much as the doomed one. The
  `stop_reason` signal distinguishes them; a blunt cap cannot.
- **Record per-attempt cost from inside the provider (provider holds the ledger)**
  — rejected: the ledger belongs to the cost-recording wrapper, not the adapter
  (ADR 0030 keeps attribution at the call surface). Attaching billed usage to the
  raised error threads the spend out without coupling the adapter to the ledger.
- **Add `usage`/`model` to the `Provider` ABC return contract** — rejected as
  out of scope: the additive optional attributes on `ProviderError` change no
  method signature and no ABC; old `except ProviderError` sites ignore them.

## Consequences

- New `errors.EmitTruncated(MalformedOutput)`; `ProviderError.__init__` gains
  optional `usage`/`model` (TYPE_CHECKING import of `TokenUsage`; `errors.py` gains
  `from __future__ import annotations`). No method signature or ABC change.
- `AnthropicProvider`: new `_is_truncated`, `_emit_truncated`, `_with_usage`;
  truncation halt at both emit-parse sites; billed usage attached at all
  post-billing raise sites.
- `AgentRunner._with_structural_retry` re-raises `EmitTruncated` before the
  `MalformedOutput` retry handler. The existing exhaustion/no-progress tests stay
  green (they raise plain `MalformedOutput`); a new test covers the truncation
  re-raise (one call, no `AgentFailure` wrap).
- `CostRecordingProvider` records billed-but-raised spend as `FAILED`. The ADR
  0030 success-path test is unchanged; new tests cover the raise path (recorded)
  and the no-usage path (skipped).
- One existing adapter test (`..._surfaces_truncation_verdict_from_stop_reason`)
  asserted the *old* fall-back-and-recover behavior; updated to assert the verdict
  still logs **and** the call now halts with `EmitTruncated`.
- Worst-case Extractor calls for a truncating blog drop from ~6–9 full 16K-token
  regenerations to **one** emit, then a clean halt; combined with the
  now-effective fail-fast, a systemically-truncating blog aborts after 2 runs.
- **Still unhandled (surfaced, not fixed — unchanged from ADR 0032):**
  `chain_steps` has no schema maximum, so a sufficiently long blog produces a spec
  that exceeds *any* fixed `max_tokens`. It now fails fast and cheap (this ADR)
  instead of burning the budget, but it still fails — the real fix is streaming +
  chunked/continuation emit (P4), deliberately out of scope here.
