# 0095 — The `affected_platforms` Layer-2 cross-check is moot by design; reconcile `validation.md §6.5` to `§4.4`

**Date:** 2026-06-17
**Phase:** 2 (post-Task-5 reconciliation)
**Deciders:** maintainer (architect — ruling), implementing agent (edit)
**Architecture refs:** `validation.md §6.5` (semantic cross-check — the bullet reconciled here),
`schema.md §4.4` (affected platforms are facet-derived, not a separate field). Resolves the drift
**surfaced** in [ADR 0094](0094-semantic-cross-check-validator.md) decision 4 (D4).

## Context

Task 5 (ADR 0094 D4) surfaced a drift it deliberately did **not** resolve: `validation.md §6.5`
specified an `affected_platforms` consistency check ("if the manifest's core block has an
`affected_platforms` field … Layer 2 verifies it matches what's derivable from `target:*` facets"),
but `schema.md §4.4` states affected platforms are **derived** from the `target:*` facets and **not
duplicated** on the core block, and `CoreBlock` (the locked Task-1 schema) declares no such field
and is `extra="forbid"`. So a Layer-1-valid manifest can never carry the field; the check has no
left-hand operand and can never fire. Task 5 reserved a code (`INCONSISTENT_AFFECTED_PLATFORMS`) and
surfaced the contradiction for the architect rather than implementing dead code or breaking the
manifest lock.

## Decision (architect ruling)

**`§4.4` wins; the check is unnecessary by design, not merely deferred.** Platforms are a
**facet-derived, single source of truth**: the `target:*` facets *are* the platform set, validated at
Layer 1 via registry membership. There is no independent `affected_platforms` store that could *be*
inconsistent, so there is nothing for a Layer-2 cross-block check to verify — the check is **moot**,
which is categorically different from the genuinely Phase-3-deferred inert checks (the
`references_lab_outputs` code-vs-manifest pair, which *will* fire once generated IaC exists).

Because the code can never be produced, keeping it "reserved" would blur exactly that distinction —
a future reader would read `INCONSISTENT_AFFECTED_PLATFORMS` as "deferred, implement later" like its
neighbours. So it is **removed**, not kept-and-labelled:

1. **`docs/validation.md §6.5`** — the `affected_platforms` bullet is rewritten to state the check is
   moot by design: platforms are facet-derived (`§4.4`), validated at Layer 1; the core block carries
   no `affected_platforms` field to drift; no Layer-2 cross-check is needed or possible. The prior
   text (which anticipated a forbidden field) is reconciled away.
2. **`validators/semantic_cross_check_validator.py`** — the `INCONSISTENT_AFFECTED_PLATFORMS`
   enum member is deleted; the module + `validate()` docstrings now describe the check as
   moot-by-design (not vacuous-reserved). The two genuinely-reserved Phase-3 codes
   (`UNDECLARED_LAB_OUTPUT_REFERENCE` / `UNDECLARED_LAB_RESOURCE_REFERENCE`) stay — they are deferred,
   not moot.
3. **Test** — `test_reserved_phase3_codes_have_no_route` drops the removed code from its loop; the
   remaining reserved Phase-3 codes still raise from `responsible_agent_for`.

This is an architecture-tier doc edit made under ADR 0084 (agent owns `docs/` edits, surfaced never
silent) on the maintainer's explicit instruction; it is surfaced in the execution log and this ADR.

## Consequences

- `§6.5` and `§4.4` no longer contradict each other; the single-source-of-truth (facets) is the
  documented and enforced reality.
- The semantic-cross-check code vocabulary now contains only live checks + genuinely-deferred Phase-3
  codes — no can-never-fire member to mislead a future implementer into "implementing the missing
  check."
- No behaviour change: the check was never implemented (ADR 0094 reserved a code but ran no check), so
  removing the code and the doc claim is a pure reconciliation. `just verify` stays green.
- If a future schema bump ever *does* add a user-editable `affected_platforms` field (re-introducing a
  second platform store), that bump reintroduces both the field and the check together, with its own
  ADR — but that is a `§4.4` change, not this one's concern.

## Alternatives considered

- **Keep the reserved code, labelled "moot per §4.4."** Rejected (architect discretion exercised
  toward removal): the genuinely-deferred Phase-3 codes share the same enum, so a labelled-moot member
  blurs the deferred-vs-moot distinction the ruling draws; removing it sharpens the vocabulary, and
  the rewritten `§6.5` leaves no dangling reference to a missing code.
- **Implement the check defensively anyway.** Rejected (already rejected in ADR 0094): unreachable
  dead code on a field the schema forbids.
- **Add the `affected_platforms` field to `CoreBlock` so the check has an operand.** Rejected: a
  manifest-lock violation (Task 1) *and* a `§4.4` violation (duplicates the facet-derived platforms),
  re-creating the very drift this resolves.
