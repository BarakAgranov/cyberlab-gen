# 0096 — `extract` / `plan` are developer / eval commands, not user-surface verbs

**Date:** 2026-06-17 (corrected the same day — see Correction)
**Phase:** 2 (Task 6 pre-work — a surfaced architectural question, then corrected)
**Deciders:** maintainer (architect — ruled, then corrected the ruling), implementing agent
**Architecture refs:** `architecture.md §2.1` (the four user-facing verbs + the new "Developer / eval
commands" subsection — reframed by this ADR), `§2.3` (the four-verb user-interface diagram). Frames
ADR 0013 (the original four-verb scaffold), ADR 0024 (`extract`'s engine), ADR 0097 (the `plan` verb).

## Correction (supersedes the original decision below)

The original decision — **Framing 2: "permanent staged entry points that coexist with `generate`"** —
was **wrong** and is superseded. The research it rested on was *factually* correct (the live
`architecture.md §2.1` did list `extract`/`plan` as user-surface verbs, with an "or stage-by-stage"
clause that the Phase-2 Task-0 reconciliation had added), but the **normative** reading was wrong: that
§2.1 text is itself the defect, not the contract.

**The decision (architect ruling): `extract` and `plan` are developer / evaluation commands only.**
They each run a *single* pipeline stage in isolation, so a stage can be built, tested, and evaluated on
its own. They have **no use for a real user** — the user-facing pipeline is `generate <url>`, which runs
the same stages internally. They are **not** part of the user surface: neither permanent user verbs (the
rejected Framing 2) nor transitional scaffolding to be deleted when `generate` ships (Framing 1) — they
persist as internal tooling, deliberately fenced off from the user interface.

**What this ADR changes (architecture-tier, ADR 0084):**
1. **`architecture.md §2.1`** — `extract`/`plan` are removed from the "User-surface commands" list
   (restoring the four user verbs `generate`/`validate`/`fix`/`telemetry submit`) and moved into a new
   **"Developer / eval commands (not part of the user surface)"** paragraph; the "or stage-by-stage"
   clause is dropped. This corrects the Task-0 edit. The §2.3 four-verb diagram (already correct) is
   untouched, and §2.1 now agrees with it.
2. **`CLAUDE.md`** — the status note frames them as developer/eval commands, not "permanent staged
   entry points."
3. **`--help`** — `extract`/`plan` are grouped under a "Developer / eval commands" rich-help panel:
   **visible and discoverable** (a developer must find them), **not** hidden, but separated from the
   user verbs so a real user can't mistake them for the intended interface. The §5.5 decode-debt
   principle: an internal command that reads like a user command is debt.

No behaviour change to the stages or the verbs themselves — `extract`/`plan` keep working; this is
framing + surfacing only.

## Context (original — retained for the record)

The product spec's headline command is `cyberlab-gen generate <url>` — one pipeline, stages internal.
Phase 1 shipped an `extract` verb and Phase 2 added a `plan` verb (per-stage entry points). The
architect flagged the decode-debt risk: a reader/agent should not have to guess whether `extract`/`plan`
are scaffolding, permanent user verbs, or internal tooling. The Phase-2 Task-0 reconciliation had edited
§2.1 to list them as user-surface verbs with an "or stage-by-stage" clause — and that edit, not the
spec, is what this ADR corrects.

## Original decision (SUPERSEDED by the Correction above)

The first pass concluded Framing 2 (permanent per-stage entry points coexisting with `generate`),
reasoning from §2.1's then-current both/and prose and the Task-0 "locked staging" note. That conclusion
is withdrawn: §2.1's prose was the Task-0 defect, and the correct call is developer/eval-only.

## Consequences

- The user surface is exactly four verbs (`generate`/`validate`/`fix`/`telemetry submit`); §2.1 and the
  §2.3 diagram now agree.
- `extract`/`plan` remain available for stage-level development and evaluation, clearly fenced off in the
  docs and grouped (not hidden) in `--help`.
- The "is this verb for users?" decode-debt is closed — in §2.1 itself, not only in derived notes.

## Alternatives considered

- **Framing 2 — permanent user-surface peers of `generate`.** The original (wrong) decision; superseded.
  `generate` is the only user entry point.
- **Framing 1 — transitional scaffolding, deleted when `generate` ships.** Rejected: they stay, as
  dev/eval tooling.
- **Hide `extract`/`plan` from `--help` entirely.** Rejected (architect): a developer must still discover
  them — group-and-label, don't suppress.
