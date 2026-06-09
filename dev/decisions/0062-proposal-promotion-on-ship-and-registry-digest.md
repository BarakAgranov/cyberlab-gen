# 0062 — Implement proposal promotion gated on spec-shipping + the Extractor registry digest

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — item **E1**)
**Architecture refs:** `schema.md §4.16` (proposal lifecycle + per-run cap as bounded steering),
`registry-details.md` (overlay metadata on ship). **Implements ADR 0050** (the design); builds on ADR
0044 (propose→accept→overlay loop) and ADR 0015/0045 (proposal shapes). The over-cap stopgap points at
**ADR 0063** (the loop-budget threading work-stream).

## Context

ADR 0050 settled E1: promotion to the shared overlay is gated on the spec shipping (not a separate
jury-proposal gate), with a write-time dedup/merge-check; `--auto` never writes un-promoted proposals;
over-cap becomes bounded in-loop steering, not a hard halt; and a registry digest is surfaced to the
Extractor so it can check novelty before proposing. The maintainer chose the **CLI-feasible slice now**,
with the over-cap clause as an explicit stopgap (the true in-loop steering needs ADR-0049 caps threaded
into the extract loop — ADR 0063).

## Decision

**1. The overlay write moves from proposal-acceptance time to spec-SHIP time.** In both modes the
overlay write now happens only *after* `write_attack_spec` puts the spec on disk:
- `--auto`: `_drive_auto` ships first, then `_promote_proposals_auto` writes to the overlay. The old
  pre-ship `_auto_accept_proposals` write (and its over-cap `ProposalCapExceeded` raise) is gone.
- `--interactive`: the per-proposal Accept/Edit menu now *collects* the reviewed proposals
  (`_collect_proposals_interactive`, no write); the overlay write (`_promote_proposals_interactive`,
  `approval='human'`) runs only after ship. This fixes the **orphan-write bug**: a budget/user abort
  after the review used to leave overlay entries with no shipped spec.

A run that does not ship (out-of-scope `--auto` halt, budget abort, user abort, jury reject, mechanical
halt) now promotes **nothing**.

**2. Over-cap is bounded steering, not a hard halt (STOPGAP).** `_promote_proposals_auto` writes up to
the per-run cap (`auto_accept_to_overlay`, with the within-overlay replace-by-key dedup as the write-time
merge-check) and **reports** the remainder — never halts, never silently drops. This replaces the
`ProposalCapExceeded` halt (the class remains in `errors.py` but is no longer raised). This is an
explicit **stopgap with expiry**: the proper fix steers the Extractor *in-loop* to reuse/refactor,
bounded by the ADR-0049 caps — the loop-budget threading work-stream, **ADR 0063**. The registry digest
(below) is the first lever toward it.

**3. Registry digest surfaced to the Extractor.** A new module function `build_registry_digest(registries)`
renders a compact, names-only digest of the four LLM-proposable vocabulary registries (`value_types`,
`facets`, `thesis_types`, `execution_contexts`) — **not** `external_data_sources` (a tool-adapter
catalog, never proposable; ADR 0055/0058). `_build_user_turn` injects it alongside the source, before
the blog. The Extractor prompt's "propose on the first pass" section is reconciled to reference the
digest ("a term in the digest needs no proposal"). This removes the "you do not see the registries"
blind-proposing that drove systematic structural-retry re-extractions (investigation 0002 §6 / 0001),
making B-ii's proactive-proposal lever effective.

**4. Provisional within-run resolution is unchanged.** The static-schema validator still treats a
proposed-but-unregistered term as a provisional pass (`PendingProposals`, ADR 0044), so the spec
validates and proceeds; only the *global* overlay write is deferred to ship.

## Consequences

- **No orphan overlay entries**; `--auto` promotes only terms from specs that shipped (the real ADR-0050
  guardrail). The shipped spec's vocabulary is always globally resolvable.
- **Digest placement & caching:** the digest sits in the *user* turn (with the blog), which the ADR-0059
  caching does not cover (the blog-prefix CachePoint is deferred), so it does not perturb the cached
  static system prefix.
- **Deferred (noted, not done here):** a bundled-overlap skip (a proposal duplicating a *bundled* term
  → no redundant overlay row) — the digest makes the model avoid this; the within-overlay replace-by-key
  is the write-time merge-check ADR 0050 step 4 requires. Sourcing `proposed_by_model` from the billed
  ledger is the provenance-family item (next, ADR 0065), kept separate to stay bisectable.
- **Tests:** the old `test_auto_over_cap_halts_without_writing` becomes "writes up to cap + reports
  remainder"; new tests pin that a budget abort in either mode promotes nothing; the digest content +
  injection are pinned.
- **No `docs/` edit** — `schema.md §4.16` already frames the per-run cap as bounded steering (ADR-0050
  design pass).
