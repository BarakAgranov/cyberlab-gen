# 0027 — AnthropicProvider call bodies: structured output, cost, model resolution

**Date:** 2026-06-01
**Phase:** 1 (post-Task-8 keystone — fill the Phase-0 adapter stub)
**Architecture refs:** `provider-interface.md` §4/§5/§6, `pipeline.md` §3.7,
ADR 0009 (error hierarchy), ADR 0018 (two-layer structural-retry budget)

## Context

Phase 0 shipped `cyberlab_gen/providers/anthropic_provider.py` as a scaffold whose
`complete` / `complete_with_tools` raised `NotImplementedError("Phase 1")`. The
Phase 1 Task 2 call surface and all agents were only ever exercised against
`MockProvider`; the first real API call (the eval) hit the stub. This ADR records
the decisions made filling the real bodies. The locked `Provider` ABC, `Message`,
`ProviderResponse`, `TokenUsage`, and the error hierarchy were **not** changed.

## Decisions

1. **Structured output via forced tool use.** The declared `output_schema` is
   exposed to the model as a single "emit" tool (`input_schema =
   output_schema.model_json_schema()`) and `tool_choice` forces a call to it;
   the tool's `input` is parsed with `output_schema.model_validate`. We do **not**
   parse free text or fenced JSON. Rationale: forced tool use is the robust,
   documented mechanism for schema-bound Claude output and gives a clean
   validation point for the malformed-output retry. The raw `anthropic` SDK is
   used directly (the project pins `anthropic>=0.40`); `pydantic-ai` is not used
   in the provider layer.

2. **Malformed-output retry = 2 attempts (ADR 0018), re-prompted.** On a
   validation failure (or a missing forced tool call) the adapter re-prompts with
   the parse error as a `tool_result` error block, up to
   `MALFORMED_OUTPUT_RETRIES.max_attempts` (2, lowered from 3 in the prior ADR
   0018 doc pass), then raises `MalformedOutput`. The call surface's *stage*
   structural-retry sits above this (Layer B of ADR 0018); the adapter owns only
   the provider-internal layer (Layer A).

3. **Transient retry / hard-failure classification.** `APITimeoutError` /
   `APIConnectionError` and `APIStatusError` with status 429 or ≥500 retry with
   backoff per `TRANSIENT_RETRIES`, then raise `TransientFailure`. Any other
   `APIStatusError` (4xx: auth, 400, 404) and any other `AnthropicError` raise
   `HardFailure` with no retry (`provider-interface.md` §6.1/§6.3). No mid-call
   vendor fallback (forbidden, `pipeline.md` §3.7).

4. **The adapter resolves its own model from the bundled rankings.** The
   `Provider` ABC passes a `CapabilityHint`, not a model id. The adapter loads
   `model_rankings.yaml` and uses the first `anthropic` entry for the capability —
   the same choice `ProviderRegistry` makes when anthropic is the winning
   configured provider, so the two agree by construction.

5. **Cost = accumulated usage across all billed calls, costed once.**
   `ProviderResponse` carries a single `TokenUsage`, but a `complete_with_tools`
   loop makes one billed call per iteration (plus any malformed retries). The
   adapter sums `input/output/cache_read/cache_write` tokens across every billed
   call and computes `cost_usd` once via `cost_ledger.compute_cost`. Summing is
   the only honest single figure the locked return type can carry. **If the
   resolved model is absent from `pricing.yaml`, the adapter raises `HardFailure`
   rather than reporting `Decimal("0")`** — a billed call with un-computable cost
   would silently corrupt budget tracking.

6. **Cache-write 5min/1h split (the question deferred to this task by
   `cost_ledger.py`).** Finding: the Anthropic SDK *does* expose the split via
   `usage.cache_creation.ephemeral_5m_input_tokens` / `.ephemeral_1h_input_tokens`
   (the 1-hour-cache beta), with the flat `usage.cache_creation_input_tokens` as
   the total. **Decision: keep the single `TokenUsage.cache_write_tokens` field.**
   This adapter never sets `cache_control` (no prompt caching in Phase 1), so
   cache-write is normally 0; when prompt caching is added it defaults to the
   5-minute TTL, which is exactly the rate `compute_cost` bills. The adapter logs
   a warning if it ever observes 1-hour cache-write tokens, so the split is
   revisited (not silently mis-billed) before 1-hour caching is relied on.

## Flags for the maintainer (genuine issues surfaced, not invented away)

- **`CostLedger` is not wired into the call surface.** `AgentRunner`
  (`cyberlab_gen/agents/call_surface.py`) records **no** `CostLedgerEntry` rows —
  there is no integration point for the per-attempt ledger entries described in
  `cost_ledger.py` (a retry-2-success call → 2 FAILED + 1 SUCCESS). The adapter
  populates `ProviderResponse.usage` correctly, but nothing accumulates it into a
  run-level `CostLedger`. Wiring the ledger into the call surface / orchestrator is
  follow-up work (Task-6-adjacent); the per-attempt outcome granularity also can't
  be expressed through the single-`usage` `ProviderResponse` return without a
  signature change.
- **`model_rankings.yaml` likely points at unserved model ids.**
  `high_quality_reasoning` and `long_context_extraction` resolve to
  `claude-opus-4-7` (and `-4-6`), but the current Opus is `claude-opus-4-8`
  (`claude-haiku-4-5-20251001` and `claude-sonnet-4-6` are current). A real
  `extract` (which uses `long_context_extraction`) will likely get a 404 →
  `HardFailure` at the provider. `pricing.yaml` also carries the 4-7/4-6 entries.
  **Not changed here** (model identity per capability is a registry/architecture
  decision, not the adapter's): flagged for the maintainer to update
  `model_rankings.yaml` + `pricing.yaml` to current ids. The live smoke test
  deliberately uses `fast_cheap_structured_output` → `claude-haiku-4-5-20251001`,
  a current, served, priced model.

## Validation status

Loop/retry/cost logic is unit-tested offline against an injected fake client
(`tests/unit/providers/test_anthropic_provider.py`, 15 tests). The **real** API
call + cassette (`tests/integration/test_anthropic_provider_live.py`) could **not
be recorded in the implementation environment**: `ANTHROPIC_API_KEY` was not
present in any reachable scope (process env, User/Machine env, the bash login
profile, `.env`). Per the task's hard rule, the cassette must come from an actual
successful call and was not hand-authored. The live test skips loudly until a
cassette is recorded (`--record-mode=once` with a key) and then replays offline
forever. **The "real API call succeeds" exit criterion is therefore PENDING that
recording — it is not satisfied by this commit.**
