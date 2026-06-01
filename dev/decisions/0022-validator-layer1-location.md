# 0022 — Validator Layer 1 lives in `cyberlab_gen/validators/layer1.py`

**Date:** 2026-06-01
**Phase:** 1 (Task 6)
**Architecture refs:** `validation.md §6.4`, `validation.md §6.10`, `coding-conventions.md §3.1`, `architecture.md §1.6`

## Decision

Validator Layer 1 (static schema validation + registry reference resolution +
`spec_kind` discriminator enforcement) ships as a new `validators` subpackage:
`cyberlab_gen/validators/layer1.py`, re-exported through
`cyberlab_gen/validators/__init__.py`. The layer's public surface is
`Layer1Validator.validate(spec) -> Layer1Result`, where `Layer1Result` is an
`InternalModel` carrying `passed: bool` and `findings: list[Layer1Finding]`. The
later mechanical layers (2, 3, 5) will land beside it in the same subpackage in
Phase 2+.

## Context

The Phase-0 project map (`CLAUDE.md`) names seven subpackages (`cli`,
`framework`, `agents`, `schemas`, `providers`, `registries`, `state`) — no
`validators`. The Task-6 brief and `coding-conventions.md` both anticipate
`cyberlab_gen/validators/layer1.py`, but the directory did not exist yet, so the
location is a real (if lightly-constrained) choice the implementer must record.

The Validator is **framework code, not an agent** (`validation.md §6.1`,
`architecture.md §1.6`): it runs deterministic checks and never invokes an LLM.
It could plausibly live under `framework/`, but the docs consistently treat the
Validator as its own component with five numbered layers, and Phase 2 adds three
more layers plus a report aggregator. A dedicated subpackage keeps the
layer-per-module growth path clean and matches the `coding-conventions.md`
anticipation.

## Alternatives considered

- **`cyberlab_gen/framework/validators/layer1.py`** — rejected: the Validator is
  a distinct architectural component with its own doc (`validation.md`) and a
  multi-layer growth path; burying it under `framework/` obscures that and
  conflicts with the `coding-conventions.md` anticipation of a top-level
  `validators/`.
- **A single `cyberlab_gen/validators.py` module** — rejected: Layers 2/3/5 are
  substantial and land in Phase 2; a package with one module per layer scales
  better than one growing file.

## Consequences

- A new top-level subpackage `cyberlab_gen/validators/` is added to the project
  map (the `CLAUDE.md` map should gain a `validators/` line; flagged for the
  maintainer, not edited here since `CLAUDE.md` is operating-notes, not `docs/`).
- The orchestrator imports `Layer1Validator` from
  `cyberlab_gen.validators`. Layer 1 returns findings; it never mutates the spec
  and never decides routing — the orchestrator reads the result and routes
  (`architecture.md §1.5`, `validation.md §6.10`).
