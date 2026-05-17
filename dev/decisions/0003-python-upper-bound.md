# 0003 — Python version upper bound `<3.15` with CI matrix

**Date:** 2026-05-17
**Phase:** Phase 0 (Task 0 setup)
**Architecture refs:** `docs/coding-conventions.md §1.1`,
`dev/phase-briefs/phase-0-agent-brief.md` Task 0 step 3

## Decision

Pin `requires-python = ">=3.13,<3.15"` in `pyproject.toml` and run CI against
both Python 3.13 and 3.14 in a matrix. Keep `pyright`'s `pythonVersion = "3.13"`
so strict-mode typing is checked against the lower bound.

## Context

`coding-conventions.md §1.1` specifies `requires-python = ">=3.13,<3.14"`.
That cap predates Python 3.14's stable release (2025-10-07) and is now stale:
3.14 is the current latest stable Python, and 3.15 is in beta (first beta
2026-05-07, final scheduled for October 2026).

The cap's original justification — "upgrading Python is an explicit decision
rather than something a contributor's local environment forces" — still
applies. The cap *value*, not the cap *principle*, needs updating.

The cap is set in `pyproject.toml`, which is implementation surface, not the
architecture contract. This ADR does not update `coding-conventions.md`; the
doc edit is surfaced separately to the architect.

## Alternatives considered

- **`<3.14` (literal carry-over from conventions).** Rejected: locks
  contributors out of 3.14, the current stable. There is no engineering reason
  the project can't run on 3.14, and forcing 3.13-only is anti-contributor
  friction with no upside.
- **No cap (`>=3.13`).** Rejected: violates the principle behind §1.1's narrow
  cap. Without an upper bound, a contributor with 3.15-beta on PATH could
  silently exercise 3.15-only behavior the rest of the team can't reproduce.
  The pyright `pythonVersion = "3.13"` setting catches some of this but not
  runtime-only divergences (e.g., changed stdlib defaults between minor
  versions).
- **CI matrix only, no pyproject cap.** Rejected as sole mechanism: CI matrix
  catches divergences already in code but does not *prevent* contributors from
  depending on 3.15 features in the first place. The cap is the prospective
  guard; the matrix is the retrospective check. They are complementary, not
  substitutes.
- **`<3.15` + CI matrix on 3.13 and 3.14 (chosen).** Allows current stable
  (3.14) and one-back (3.13). Blocks 3.15-beta usage. The CI matrix verifies
  the declared range is actually exercised.

## Consequences

- `pyproject.toml` uses `requires-python = ">=3.13,<3.15"`.
- The CI workflow runs `verify` on a matrix of Python 3.13 and 3.14
  (`fail-fast: false` so both legs report).
- `pyright`'s `pythonVersion = "3.13"` keeps strict-mode typing pinned to the
  lower bound; 3.14-only syntax can't sneak in.
- When Python 3.15 ships stable (October 2026), a follow-up ADR revisits the
  cap.
- `coding-conventions.md §1.1`'s stale `<3.14` cap is a known doc bug to be
  addressed as a separate architecture-level edit, not by this implementation
  task.
