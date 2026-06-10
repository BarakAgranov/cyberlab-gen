# 0078 — The Extractor-Jury gets a verify-only tool set; the read/write split is enforced by tool availability

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Tier ④, done now on the new `ToolUsingAgent` contract; [CONTRACT])
**Architecture refs:** `architecture.md §1.5` (LLMs never modify shared state outside their
designated output; the split is enforced by *tool availability*), `agents.md §5.5` (the
Extractor-Jury and its tool inventory), ADR 0072 (`ToolUsingAgent`). Source: investigation `0004
§1.4` SHOULD-FIX row.

## Context

`architecture.md §1.5` says the read/write (verify/propose) split between agents is enforced by
**tool availability** — an agent that must not write simply is not handed the write tools. The
`ExtractorJury` violated this: it ran the **full** Extractor tool inventory, including the three
`propose_*` write tools, with the prohibition on proposing stated only in **prose**
(`extractor_jury/prompt.md`). There was no live leak — the jury ignores its executor, so any
proposals it made were discarded — but the `§1.5` enforcement was a prose rule, not mechanical, and
the pattern would propagate to every Phase-2 reviewer (Planner-Jury, Critic).

A doc tension underlies it: `agents.md §5.5` says the jury "has the **same tool inventory** as the
Extractor so it can independently verify `external_api` responses." Taken literally, "same
inventory" includes the `propose_*` tools — but those were never needed for *verification*; only
`external_lookup` is. So `§5.5`'s **intent** (verification capability) is fully served by
`external_lookup`, and the "same inventory" phrasing over-states the requirement relative to `§1.5`.

## Decision

Per the authority gradient (`architecture.md §1.5` > `agents.md`), resolve toward `§1.5`: a
**verify-only tool set**, enforced mechanically at two layers.

- `extractor_tool_definitions(verify_only=True)` advertises **only** the read/verify
  `external_lookup` tool; the `propose_*` write tools are withheld, so the model is never offered
  them.
- `ExtractorToolExecutor(verify_only=True)` refuses a `propose_*` call at execution (returns an
  error result, records no proposal) — defense-in-depth behind the withheld advertisements.
- `ToolUsingAgent` gains a `verify_only_tools` flag that threads both; the `ExtractorJury` sets it.
  The jury **keeps** `external_lookup`, so it still independently verifies `external_api` responses
  (`§5.5`'s actual intent). Phase-2 reviewers inherit the enforcement by constructing the contract
  with `verify_only_tools=True`, never a prose rule.

## Alternatives considered

- **Leave the prohibition in prose** — rejected: `§1.5` requires the split be enforced by tool
  availability; prose is exactly what this fix replaces, and the pattern would propagate.
- **A separate jury tool module** — rejected as premature: the jury's verify set is a strict subset
  of the Extractor's, so a `verify_only` filter on the one tool-definitions function is the minimal
  faithful split; Phase 2 can generalise per-agent tool sets at second use.

## Consequences

- The read/write split is now mechanical (tool advertisement + executor guard), not prose; pinned by
  tests (verify-only defs advertise only `external_lookup`; a verify-only executor refuses
  `propose_*`; the jury is wired `verify_only`). No shipping-behaviour change — the jury's proposals
  were already discarded.
- Phase-2 reviewers (Planner-Jury, Critic) inherit the enforcement through the `ToolUsingAgent`
  contract.
- **Doc-clarification surfaced, not silently edited** (per `CLAUDE.md`): `agents.md §5.5`'s "same
  tool inventory as the Extractor" now slightly over-states it — the jury's inventory is the
  *verification subset* (it keeps `external_lookup`, not the `propose_*` write tools). Routing the
  wording fix to `agents.md` is the user's call; the code resolves the `§1.5`/`§5.5` tension toward
  `§1.5` and records it here.
