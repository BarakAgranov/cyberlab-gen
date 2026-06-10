# 0076 — Work-stream: the pickle-fallback persisted-spec surface needs a durable encode + a picklability guard

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Tier ② tracking-promotion; ②.1)
**Status:** Tracked work-stream (not yet built — promoted from a one-line execution-log note to an
owning ADR so it cannot rot before Phase 2 grows the persisted surface).
**Architecture refs:** ADR 0040 (LangGraph checkpointing), ADR 0066 (`Provenance[T]` picklability
fix + the serializer allowlist), `dev/phase-1-execution-log.md` (the originating one-sentence note).
Source: investigation `0003 §1.2` (3-A — the genuine latent carry-forward).

## Context

`framework/checkpointing.py` sets `pickle_fallback=True`: the `HttpUrl`-bearing `AttackSpec` subtree
rides **pickle** (it is absent from the msgpack allowlist). This is **sound today** — every nested
type is picklable, the round-trip + mid-abort tests are green, and the `Provenance[Severity]` crash
that proved the class is reachable was fixed (ADR 0066). The exposure is forward-looking: a Phase-2
manifest / IaC type added to the persisted surface inherits `pickle_fallback` with **no mechanical
picklability guard**, so a future non-picklable nested type would fail only at checkpoint time, on a
real run.

This was tracked as a single execution-log sentence — too thin to survive Phase 2. This ADR owns it.

## Decision (scoped, not built now)

Before Phase 2 grows the persisted-spec type surface, do **one or both** of:

1. **Durable encode** — give `HttpUrl` an explicit msgpack encode/decode hook and drop
   `pickle_fallback`, so the whole persisted-spec surface rides the typed serializer (no pickle).
2. **Mechanical picklability guard** — a smoke test that walks the persisted-spec type surface (the
   types reachable from `AttackSpec` / the checkpointed `PipelineState`) and asserts each is
   round-trippable through the configured serializer, so a newly-added non-serializable type fails
   in CI rather than on a live run.

Do **not** fire-drill the rewrite now — the current behaviour is correct and tested. Capture is the
point: the guard must exist *before* Phase 2 adds the first new persisted type.

## Consequences

- A single searchable home for the picklability risk, with a concrete durable fix + a CI guard
  scoped, so the Phase-2 author adding a `LabManifest` / IaC type to the checkpoint surface has a
  test that catches a non-serializable field immediately.
- No code change in this ADR — it is a tracking promotion.
