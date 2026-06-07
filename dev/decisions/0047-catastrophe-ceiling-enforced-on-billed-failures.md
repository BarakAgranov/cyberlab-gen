# 0047 — The catastrophe ceiling is enforced on billed failures, not only successes

**Date:** 2026-06-07
**Phase:** 1 (operational hardening)
**Architecture refs:** `provider-interface.md §5` (cost tracking; the framework — not the
provider — owns budget-overrun decisions, §5.3). **Amends ADR 0038** (one high
catastrophe ceiling, enforced mid-run), which enforced it only on the success path.
Builds on ADR 0033 (billed-on-raise accounting).

## Context

ADR 0038 enforces the catastrophe ceiling in `CostRecordingProvider` after each
*successful* billed call, and explicitly argued the failure path needs no check: "A
*failed* call that crosses the line needs no extra abort — its own `ProviderError`
already halts the run and its spend is recorded" (ADR 0038, §"One high catastrophe
ceiling").

That premise is false for the failure modes that actually dominate a runaway. A billed
failure's error does **not** reliably halt the run:

- `MalformedOutput` (a billed, schema-invalid emit) is **caught and retried** by the call
  surface's structural-retry loop (`agents/call_surface.py` `_with_structural_retry`, up
  to `1 + 2` attempts) and, on exhaustion, by the orchestrator's structural-retry and the
  refinement coordinator (`architecture.md §1.7`). Each retried attempt is billed again
  (ADR 0033).
- Only `EmitTruncated` and `HardFailure` halt fast — and those are exactly the cases where
  the old premise *happened* to hold.

So a run that fails repeatedly at the emit layer accumulates real, billed spend while the
ceiling — checked only on success — is never consulted. A failure-dominated run could
overshoot the `$25` ceiling substantially before a success (if any) landed.

The wrapper cannot know whether a given error will halt or be retried — that decision
belongs to the layers above it. Its job is to bound cumulative spend, which it must
therefore do on **every** billed call, success or failure.

## Decision

`CostRecordingProvider._record_billed_failure` now calls `_enforce_ceiling` after
recording the billed-`FAILED` entry, exactly as `_record` does on the success path. When
cumulative spend has crossed `cap_usd`, it raises `BudgetExceeded` — a `HardFailure`, so
the structural-retry / refinement machinery (which only catches `MalformedOutput`) does
not absorb it, and the run actually halts.

The originating provider error is threaded through as `BudgetExceeded.cause` and chained
with `raise ... from exc`, so escalating to the ceiling abort **preserves** the original
failure rather than masking it. Below the ceiling the failure path raises nothing new and
the caller re-raises the original error unchanged — so a normal billed failure behaves
exactly as before; only the ceiling-*crossing* failure escalates to `BudgetExceeded`.

## Consequences

- `_enforce_ceiling` gains an optional keyword `cause`; the success-path call passes
  `cause=None` (behavior unchanged), the failure-path call passes the original error.
- A failure-dominated run is now bounded by the catastrophe ceiling identically to a
  successful one — the gap ADR 0038 left.
- New test `test_cost_recording_provider_trips_ceiling_on_billed_failures`: a sequence of
  billed `FAILED` calls trips the ceiling, the crossing call escalates to `BudgetExceeded`,
  and the original `EmitTruncated` is preserved as `__cause__` / `.cause`. The existing
  success-path ceiling test is unchanged.
- ADR 0038's "failed call needs no extra abort" rationale is superseded; the module and
  `_record_billed_failure` / `_enforce_ceiling` docstrings in `cost_recording_provider.py`
  are corrected to match.
