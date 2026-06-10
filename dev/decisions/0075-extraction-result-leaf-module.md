# 0075 — ExtractionResult moves to a leaf module; the agents↔framework cycle is dissolved

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch B, ①.6 ride: leaf-module move)
**Architecture refs:** `architecture.md §1.5` (the agents/framework split), ADR 0054 (the prior lazy
import). Source: investigation `0004 §1.2` (SHOULD-FIX, the import-cycle row).

## Context

There was a load-time import cycle between `agents` and `framework`, papered over by a lazy import:

- `framework.orchestrator` **runtime-imported** `ExtractionResult` from
  `agents.extractor.extractor` (the orchestrator's `PipelineState` has an `extraction:
  ExtractionResult` field).
- the Extractor's `refine` **lazy-imported** `framework.refinement` (`RefinementPatch`,
  `apply_field_patch`, `RefinementPathError`).

Because `import cyberlab_gen.framework.refinement` first runs `framework/__init__` → which imports
`orchestrator` → which imported `agents.extractor.extractor`, a *top-level* `framework.refinement`
import inside the Extractor would re-enter a partially-initialised `agents.extractor.extractor` — the
cycle the lazy import sidestepped.

## Decision

**Move `ExtractionResult` to a leaf module — `cyberlab_gen/agents/results.py` — that imports neither
`framework` nor the orchestrator.** `framework.orchestrator` (and the pipeline test fakes) import it
from the leaf; `agents.extractor.extractor` re-exports it (so `from cyberlab_gen.agents import
ExtractionResult` and the subpackage re-export chain keep working).

With the orchestrator no longer importing `agents.extractor.extractor`, the cycle is gone:
`framework/__init__` → `orchestrator` → `agents.results` (a leaf — it imports only
`agents.extractor.tools`, `agents.proposals`, and `schemas`, none of which import `framework` at
runtime). The Extractor's `framework.refinement` import is therefore promoted to **top level** and
the lazy import (and its ADR-0054 workaround comment) is removed.

## Alternatives considered

- **Move `RefinementPatch` + the patch machinery to a leaf instead** — rejected. `framework.refinement`
  is already a leaf (it imports only `errors` + `schemas`); the cycle ran through
  `orchestrator → agents.extractor.extractor` for `ExtractionResult`, so relocating the *result* is
  the targeted fix.
- **Keep the lazy import** — rejected. The fix register's goal is to *dissolve* it; the leaf move is
  what makes the top-level import safe, and a leaf home is what Phase-2 generators need to
  produce/consume result contracts without re-introducing the cycle.

## Consequences

- No load-time `agents`↔`framework` cycle; the Extractor imports `framework.refinement` at top level.
  Proven by the full suite importing and running cleanly (a cycle would surface as an `ImportError`).
- `ExtractionResult` has a stable leaf home; Phase-2 result contracts can join it there.
- Existing import paths (`from cyberlab_gen.agents.extractor.extractor import ExtractionResult`, and
  the `agents` package re-export) keep working via re-export.
- **No `docs/` edit** — a pure module-placement refactor under `architecture.md §1.5`.
