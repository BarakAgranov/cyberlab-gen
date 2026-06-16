# 0084 — Agent owns `docs/` edits (surfaced, never silent)

- **Status:** accepted
- **Date:** 2026-06-16
- **Deciders:** maintainer (architect), implementing agent
- **Supersedes:** the `CLAUDE.md` "never edit `docs/` from an implementation task" rule

## Context

`CLAUDE.md` previously barred the agent from editing `docs/` during an implementation
task: on finding a doc bug or contract drift it had to record the issue in
`dev/decisions/` and wait for the maintainer to route the fix. In practice this serialized
every doc reconciliation through the maintainer and was the single biggest source of
round-trip latency — the Phase-2 Task-0 reconciliation and the architect-review follow-ups
both stalled on it. The maintainer reviews the diffs regardless, so the gate bought process
friction, not safety.

The friction the rule *was* guarding against is real but narrower than a blanket ban: the
hazard is a contract changing **silently** — as an unannounced side effect of an
implementation edit — so the maintainer cannot see that the source of truth moved.

## Decision

The agent **owns `docs/` edits, including architecture-tier**, under one discipline:
every doc edit is **deliberate and surfaced**.

- Make the edit directly when a task touches the docs (a doc bug, a needed contract change,
  a reconciliation).
- Record the rationale in `dev/decisions/` for anything **substantive** — a changed
  contract, not a typo.
- **List every doc change explicitly** in the turn's summary so the maintainer can verify it
  ("you edit, I verify").
- **Never** change a contract silently or as an unannounced side effect of an
  implementation edit.
- A change to an `architecture.md §1.5`/`§1.6` invariant (the LLM/framework split,
  mechanical-safety-never-LLM) still requires an ADR **and** explicit maintainer sign-off
  before the agent relies on it. The authority gradient and the
  "never resolve architectural ambiguities silently" rule are unchanged.

## Consequences

- **+** Doc reconciliations land in the same pass as the code that motivates them; no
  serialize-through-maintainer round trip.
- **+** The contract still cannot move invisibly: every edit is enumerated for review, and
  invariant changes keep their hard gate.
- **−** The maintainer now verifies after the fact rather than before. Mitigated by the
  explicit per-turn enumeration of doc changes and by ADRs for substantive ones.
- The `CLAUDE.md` "Hard rules" and "Where to write things" entries were updated to match.
