# 0110 — Extractor check-then-proposes the central flowing-value's type (QUEUED — not yet implemented)

**Date:** 2026-06-21
**Phase:** 2 (recorded now; implementation queued for extract-stage work)
**Status:** **QUEUED / owned deferral** — recorded so it is not lost; **no code change lands with this
ADR**. Owner: the next extract-stage / `generate`-pipeline effort. Verified by an **extract** eval, not
the plan eval.
**Architecture refs:** `agents.md §5.4` (the Extractor is the sole proposer of value_types), ADR 0044
(propose→overlay→validate), ADR 0109 (the bundled `github_actor_id` hand-add this complements), ADR
0099 §6 (the manifest registry-membership deferral that bounds the interim exposure).

## Context

The run-20260621 codebuild post-mortem found the committed AttackSpec fixture's Extractor proposed
`github_personal_access_token` and the facet `target:github_actor_id_filter`, but **never proposed
`github_actor_id`** — the chain's *central* flowing value (the GitHub numeric actor id eclipsed to
bypass the unanchored `ACTOR_ID` regex). The Planner then had no registered type for that flow and
non-deterministically papered over it, routed back, or spiralled.

ADR 0109 hand-added `github_actor_id` to the bundled `value_types` registry so the **plan-eval**
resolves it. But the plan-eval reads a *frozen* AttackSpec fixture and is overlay-read-only, so it
cannot exercise — and ADR 0109 did **not** fix — the **production extraction** behaviour: a live
`generate` of a similar blog would still under-propose the central flowing value's type. The architect
confirmed the hand-add unblocks the plan-eval for the right contract ("Planner given a complete
registry") while this production gap is tracked here, separately.

## Decision (to implement later)

Make the Extractor reliably **check-then-propose a `value_type` for every value that flows across
steps and needs typing** — explicitly including identifier values (account / actor / resource IDs),
not just secrets/tokens. The Extractor already proposes *some* types (it proposed the PAT); the gap is
proposal **completeness** for non-secret flowing identifiers. Most likely an Extractor base-prompt
change (a completeness instruction: "every value the chain carries between steps must be typeable —
if no registered `value_type` fits, propose one"), possibly with a light coverage check. Authority is
unchanged (the Extractor proposes; the Planner never does, `agents.md §5.4`). Pin it with an
Extractor prompt-content test and confirm behaviourally on an **extract** eval.

## Why queued, not done now

- It belongs to the **extract** stage and is verified by an extract eval — orthogonal to the plan-eval
  the architect is re-running (a live extract would not change the frozen plan-eval fixture).
- It is an LLM-completeness improvement (a prompt nudge), best tuned against real extract-eval data
  rather than guessed now.

## Interim exposure (bounded)

Until implemented, a real extraction may under-propose a flowing-value type. Downstream, the Planner
either **routes back to the Extractor** (the correct, in-band signal — what codebuild's run 3 did) or,
*if* the type is missing **and** the manifest registry-membership check is still deferred (ADR 0099
§6), could **false-approve** an untyped flow (codebuild's run 1). So the worst-case interim exposure
is co-owned with the ADR-0099 §6 deferral; closing either one removes the silent-false-approve path.
For the curated set today, `github_actor_id` is the only known instance and it is registered (ADR
0109), so the acute case is closed.

## Consequences (when implemented)

- A live extraction of an identifier-pivot blog proposes the needed type, so the propose→promote→type
  chain works end-to-end without a hand-added registry entry.
- The ADR-0109 bundled `github_actor_id` entry becomes canonical vocab the Extractor simply *uses*
  (no proposal needed) rather than a stand-in for a missed proposal.
