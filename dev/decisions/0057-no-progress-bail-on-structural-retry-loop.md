# 0057 — No-progress early-bail on the structural-retry loop

**Date:** 2026-06-08
**Phase:** 1 (pre-Phase-2 safety/lossless batch)
**Architecture refs:** `validation.md §6.10` (static-schema → structural retry, never
refinement), `architecture.md §1.5/§1.7` (the framework routes; retry vs refinement).
**Extends** ADR 0032 (the same no-progress bail on the call-surface `MalformedOutput`
loop) to a second locus: the orchestrator's structural-retry loop.
**Investigation:** `dev/investigations/0001-external-sources-and-convergence.md` finding §5/⑥.

## Context

The orchestrator's structural-retry loop (`validate_node` → Extractor re-run on a failing
`StaticSchemaResult`) retried to the full per-stage budget regardless of whether the
findings were improving. The interrupted Wiz run showed the cost: it cleared 8 → 1 finding,
then would have spent the remaining structural budget (~3 more long-context Extractor
calls, ~+$3–4) re-extracting toward a **single unconvergeable finding** (an external-source
provenance label the structural gate can never resolve), reaching the same
`HALTED_VALIDATION` it would have reached immediately — just $3–4 later.

ADR 0032 already added a no-progress bail at the **call surface** (`MalformedOutput`
loop): two identical failures in a row abort early. The orchestrator's structural-retry
loop — a *different* loop, one level up — had no equivalent.

## Decision

Add a no-progress early-bail to `validate_node`, mirroring ADR 0032's mechanism (exact
equality of the failure signature, deterministic framework code):

- A new `PipelineState.last_static_finding_signature` carries an order-independent
  signature of the prior failed attempt's finding set (`sorted(rendered_findings())`,
  joined).
- On a failing validation, if the current finding set's signature **equals** the prior
  attempt's, the run **halts immediately** as `HALTED_VALIDATION` (the same terminal status
  the budget-exhaustion path produces — just reached earlier) with a `halt_reason` naming
  the no-progress condition and the recurring findings.
- A **changing** finding set may signal convergence, so the budget is honoured then — the
  signature is updated and the retry proceeds.
- The signature is **reset to `None` whenever validation passes**, so a later structural
  failure (e.g. on a refinement patch that re-enters the structural gate) is judged fresh
  against its own streak, never against a pre-pass finding set.

The bail only ever halts **earlier** than the existing budget; it never raises the ceiling,
never routes to refinement, never decides whether output ships, and **does not change the
validation contract** (what counts as a blocking finding is untouched — only *how long* the
pipeline grinds on an unconvergeable one).

## Consequences

- `PipelineState` gains `last_static_finding_signature: str | None = None` (defaulted; round
  -trips through the checkpointer). New module helper `_finding_signature`.
- The existing `test_static_schema_failure_routes_to_retry_not_refinement` is updated to
  emit a *different* invalid spec each attempt (so it still exercises the full-budget retry
  path); the new behavior — identical findings bail early — is covered by a dedicated test.
  The L3 global-cap / recursion-limit tests now drive a `ChangingBadExtractor` (distinct
  finding per run) so their runaway loops reach the caps rather than tripping this bail.
- Tests: identical findings across two attempts → halt at the 2nd attempt (not the full
  budget), `HALTED_VALIDATION` with a "no progress" reason, jury never invoked; a run whose
  findings keep changing uses the full budget and halts only on exhaustion.

## Alternatives considered

- **Lower the structural-retry budget globally.** Rejected (as in ADR 0032): a smaller
  budget hurts the genuine converging case as much as the doomed one. The no-progress
  signal distinguishes them; a blunt cap cannot.
- **LLM-judge "is this converging?".** Rejected outright — retry control flow is framework,
  never LLM (`architecture.md §1.5/§1.6`). Exact equality of the finding signature is the
  mechanical signal.
- **Wait for the MITRE/advisory category fixes (which make unconvergeable findings rare).**
  Those are the cure (a separate, sign-off-gated change); this is a cheap, contract-neutral
  backstop that helps any time the Extractor stalls on an unfixable finding.
