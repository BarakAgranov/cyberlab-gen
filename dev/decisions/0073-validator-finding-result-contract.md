# 0073 — A shared Finding/Result contract for the mechanical-validator layers

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch B, ①.6 part 2; [CONTRACT])
**Architecture refs:** `validation.md §6.4`/`§6.9`/`§6.10` (the mechanical validator layers),
`architecture.md §1.6` (mechanical safety is framework-owned). Source: investigation `0004 §1.2` (S7,
completeness).

## Context

There was no validator-layer contract. `StaticSchemaFinding`/`StaticSchemaResult` and
`GroundingFinding`/`GroundingResult` were **independent `InternalModel`s** whose findings were
byte-identical in shape — a `(code, location, detail)` triple with the same `render()` — yet defined
twice, with divergent `validate()` signatures. Every Phase-2 mechanical layer (L2/L3/L5) would have
been a bespoke type-pair plus bespoke orchestrator-node surgery, and the locator convention
(JSONPath-like `location`) had no single home to be enforced in.

## Decision

**Introduce a generic `Finding`/`FindingResult` base (`validators/base.py`) and refactor both layers
onto it.**

- `Finding[CodeT: StrEnum]` — `code`/`location`/`detail` + `render()`, generic over the layer's
  *code* enum so each layer keeps its **own closed vocabulary** (a static-schema code and a
  grounding code never collapse to interchangeable strings; the "generic Finding[CodeT]" shape the
  user chose over a single `code: str`).
- `FindingResult[F: Finding[Any]]` — `findings` + `rendered_findings()`. The bound is `Finding[Any]`
  because the result is agnostic to *which* code vocabulary its findings carry (Pydantic/pyright
  treat the code param as invariant, so `Finding[StrEnum]` would reject `Finding[StaticSchemaCode]`);
  the per-code precision lives on `Finding`, not the result.
- `StaticSchemaFinding(Finding[StaticSchemaCode])` / `StaticSchemaResult(FindingResult[…])` (adds
  `passed`); `GroundingFinding(Finding[GroundingCode])` / `GroundingResult(FindingResult[…])` (keeps
  `needs_retry` / `retry_findings`).

The `validate()` *input* signatures stay per-layer (static takes `pending`, grounding takes
`lookups`) — they genuinely differ; the contract is the finding/result *shape*, which is what every
Phase-2 layer reuses. The shared base is also the single home where the integer-index locator
convention will be enforced (the follow-on commit that canonicalises producers, ADR 0074).

## Alternatives considered

- **A single concrete `Finding` with `code: str`** — rejected (user decision): it collapses two
  closed code vocabularies into interchangeable strings, weakening the contract a Phase-2 reviewer
  reads.
- **Leave the two pairs independent** — rejected: it is the duplication the fix targets, and it
  would force every Phase-2 layer to re-derive the shape.

## Consequences

- One `Finding`/`Result` shape across the mechanical layers; `render()` + `rendered_findings()` live
  once. Both layers behave identically (the unchanged `test_static_schema_validator` /
  `test_grounding_validator` suites stay green) plus a structural test that both subclass the base
  and the grounding retry-view still works on it.
- Phase-2 layers (L2/L3/L5) subclass two parametrised classes instead of authoring a bespoke pair.
- A single place (the base's `location`) to enforce the locator convention (ADR 0074, next).
- **No `docs/` edit** — `validation.md` describes the findings/result shape; the code now shares one
  definition of it.
