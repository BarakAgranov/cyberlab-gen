# Contributing to cyberlab-gen

This is a working note for human contributors. The project is in Phase 0
(skeleton): plumbing exists but end-to-end generation does not. The docs and
dev notes below describe how the project is built and where in-flight
decisions are recorded.

## Documentation map

[`docs/index.md`](docs/index.md) is the routing table — it maps question
types ("what does X do," "how is Y structured") to the precise section of the
right doc. [`docs/architecture.md`](docs/architecture.md) is the architectural
hub.

## Code style and rules

[`docs/coding-conventions.md`](docs/coding-conventions.md) is the source of
truth. In summary:

- Python 3.13+ syntax (PEP 695 generics, `T | None`, built-in generics,
  `StrEnum`).
- Pyright in strict mode; no `Any` without an inline justification.
- Pydantic artifact models inherit from `ArtifactModel` (`extra="forbid"`);
  internal-only types from `InternalModel`. See
  [`dev/decisions/0004-base-class-discipline.md`](dev/decisions/0004-base-class-discipline.md).
- No free text passes between pipeline stages — every cross-stage boundary is
  typed.

## Build and verify gate

`just verify` runs the four gates: `ruff check`, `ruff format --check`,
`pyright`, and `pytest`. It is the gate before any commit and is re-run on
every push by CI. Other targets are listed in the [`justfile`](justfile).

## Decisions and execution logs

Non-trivial architectural choices made during implementation are recorded as
ADRs in [`dev/decisions/`](dev/decisions/) (4-digit zero-padded, sequential;
template in `docs/coding-conventions.md §7.3`).

Per-task execution notes — what was built, surprises, deferred items — go in
`dev/phase-N-execution-log.md` for the current phase. The entries are
append-only and follow the template at the bottom of the matching phase brief.

## Current phase brief

[`dev/phase-briefs/phase-0-agent-brief.md`](dev/phase-briefs/phase-0-agent-brief.md)
is the brief for Phase 0. The corresponding execution log is
[`dev/phase-0-execution-log.md`](dev/phase-0-execution-log.md).
