# 0059 — Wire Anthropic prompt caching on the provider's static prefix

**Date:** 2026-06-09
**Phase:** 1 (operational hardening — the Sysdig truncation batch)
**Investigation:** `dev/investigations/0002-extractor-emit-truncation.md` (§1 caching unwired;
§3 the structural-retry input doubling).
**Architecture refs:** `provider-interface.md` §4 (the call surface — the **locked** surface
this touches), §5.2 (the five pricing rates, incl. cache variants), `architecture.md §1.5`
(the LLM/framework split — unaffected). Builds on ADR 0036 (pydantic-ai adapter) and the
Phase-0 cost-ledger cache-rate decision (`cost_ledger.py` module docstring).

## Context

Investigation 0002 found prompt caching **unwired**: no `cache_control` / cache settings
anywhere in `providers/*.py`, and `cache_read_tokens=0` / `cache_write_tokens=0` on every
ledger entry of the real Sysdig run. Consequence: **every** model-request re-bills the full
input. For the Extractor that input is a **~41K base dominated by the schema-heavy system
prompt** (the cached normalized blog is only ~6–8K tokens), and it is re-sent byte-for-byte on
every request — each internal output-retry, each tool-loop turn, and again on the full
structural re-extraction. That re-extraction is what doubled the run's input (82,365 → 154,452)
and is also where the emit truncated. Caching the unchanging prefix removes most of that
repeated spend.

The cost machinery was **already ready**: `compute_cost` prices `cache_read` (at `cache_read`)
and `cache_write` (at `cache_write_5min`) (`cost_ledger.py`), and `_run_usage_to_token_usage`
copies `cache_read_tokens`/`cache_write_tokens` from pydantic-ai's `RunUsage`. Only the cache
**markers** were missing.

## Decision

Enable Anthropic prompt caching in the adapter's per-request settings
(`AnthropicProvider._model_settings`, `anthropic_provider.py`), via pydantic-ai 1.103's
`AnthropicModelSettings`:

- `anthropic_cache_instructions=True` — `cache_control` on the last **system-prompt** block
  (the schema-heavy instructions — the dominant share of the ~41K base).
- `anthropic_cache_tool_definitions=True` — on the **tool-definition** JSON (a no-op when a
  call has no tools, e.g. a future toolless `complete`).
- `anthropic_cache_messages=True` — on the last **message** block, which covers the
  metadata+blog prefix within a tool loop / output-retry burst.

That is **3 of Anthropic's 4 cache breakpoints**; the automatic `anthropic_cache` setting is
left **off** (pydantic-ai raises `UserError` if `anthropic_cache` and `anthropic_cache_messages`
are both set — they are not). TTL is the default **5 minutes** — within a run, requests are
seconds apart, so within-extract reuse (the multiple requests of one extract) is reliable;
cross-extract reuse lands when the gap is under the TTL. The system-prompt and tool breakpoints
are byte-stable across the whole run, so they carry across the structural re-extraction
regardless.

Applied in the shared `_invoke`, so **every** agent call benefits, not only the Extractor — the
Extractor is simply where the repeated-request cost is largest.

## What is and is not a surface change

- **The `Provider` ABC is unchanged.** `complete` / `complete_with_tools` keep their exact
  signatures; no caller changes. This is an **internal request-configuration** change.
- **The observable effect** is in the ledger: `cache_read_tokens` / `cache_write_tokens` become
  non-zero, and `cost.yaml` reflects them (priced as above). A cache write costs **1.25×** input
  on the written tokens (`cache_write_5min` = $6.25/Mtok vs $5 input for Opus); a cache read
  costs **0.1×** ($0.50/Mtok). For the repeated-prefix Extractor path the reads dominate and the
  net is a clear win; for a hypothetical pure one-shot call it is a small (~0.25× on the cached
  prefix) loss — acceptable, since the expensive calls are the repeated ones.

## Judgment calls (not pinned by the docs)

- **Provider-wide vs Extractor-only.** Provider-wide (in `_invoke`). The marginal one-shot loss
  is tiny and the code is simpler/uniform; the dominant cost (repeated Extractor requests)
  benefits most.
- **5-minute vs 1-hour TTL.** 5-minute (the `True` default). The reliable, dominant win is
  within-extract reuse (seconds apart). A 1-hour TTL would guarantee cross-extract hits but
  doubles the write rate (`cache_write_1h` = $10/Mtok); not worth it for a ~10-minute run.
- **Blog-prefix breakpoint placement.** `anthropic_cache_messages` marks the last message block;
  it captures the metadata+blog within a request burst. A dedicated `CachePoint` *between* the
  stable blog and the variable findings (for guaranteed cross-extract blog reuse) would require
  extending the `Message` content surface from `str` to a structured sequence — a further
  locked-surface change, deliberately **deferred**. The system+tools breakpoints already carry
  the dominant prefix across extracts.

## Consequences

- `AnthropicProvider._model_settings` now returns an `AnthropicModelSettings` carrying the
  output cap **and** the three cache flags; `_invoke` uses it.
- Tests: the cache flags are asserted present in `info.model_settings` (offline `FunctionModel`
  — the flags are inert there, so this checks they are *set*, which is the deterministic
  contract); a mocked multi-request run surfaces aggregated `cache_read`/`cache_write` tokens;
  cache-token **cost** stays pinned by `test_cost_ledger::test_compute_cost_with_cache_read_and_write`.
  The real cache **hit rate** is only observable on the live path (the user's re-run).
- No change to `architecture.md §1.5`/`§1.6`: caching is a billing/transport optimisation; the
  LLM/framework split is untouched.
- `just verify` green; per-item commit; no tag; no provider-backed eval run.
