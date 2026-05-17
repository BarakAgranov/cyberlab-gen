# 0004 — Base class discipline: ArtifactModel vs BaseModel

**Date:** 2026-05-17
**Phase:** Phase 0 (post-Task 2)
**Architecture refs:** `docs/coding-conventions.md §11`, `docs/schema-details.md §1`, `CLAUDE.md` hard rules

## Decision

Every Pydantic class that gets serialized to YAML inherits from one of the project's base classes (`ArtifactModel` for artifact-bound, `InternalModel` for internal scratch). `BaseModel` from Pydantic is reserved for three cases:

1. The project base classes themselves (`ArtifactModel`, `InternalModel`) inherit from `BaseModel` directly.
2. Generic type bounds (e.g., `OverlayRegistryFile[E: BaseModel]`) where the bound is intentionally permissive.
3. Test fixtures and ad-hoc internal types that never serialize.

## Context

`schema-details.md` declares roughly fifty classes (artifact inner blocks in §3-§5, registry meta-schemas in §6) as `BaseModel(extra="forbid")`. The architectural intent is `ArtifactModel`, which carries five load-bearing settings (`extra="forbid"`, `validate_assignment=True`, `str_strip_whitespace=True`, `use_enum_values=False`, `populate_by_name=True`) — see Task 1's execution log and ADR 0003.

The doc's `BaseModel + ConfigDict(extra="forbid")` pattern is drift:

- It silently drops the four other inherited settings.
- It duplicates the config line across every class, making future config changes (e.g., adding `frozen=True`) require ~50 edits.

Discovered during Task 2 plan review when implementing `ExtrasEntry`. Task 2's executed code correctly uses `ArtifactModel`.

## Alternatives considered

- **Sweep `schema-details.md` now.** Rejected: ~50 classes, several edge cases (`_ExternalSourceEntryBase` as private base; `OverlayRegistryFile`'s generic bound) need careful per-class judgment. A blanket find-and-replace would introduce bugs.
- **Leave the doc alone, implement architectural intent silently in code.** Rejected: the doc is the contract; silent drift between doc and code is the failure mode CLAUDE.md's authority gradient exists to prevent.
- **Apply this ADR as the discipline; agents transcribe per ADR, not per doc verbatim; doc fixes follow naturally as each section is transcribed.** Chosen.

## Consequences

- Tasks 3+ agents apply this ADR when transcribing classes from `schema-details.md`. When a class in the doc uses `BaseModel`, the agent uses `ArtifactModel` unless one of the three reserved cases applies. Per-class judgment calls surface in the execution log.
- `schema-details.md` is updated incrementally as each section is exercised — Task 3 updates §6, Phase 1 tasks update §4-§5 as inner blocks land.
- This ADR is referenced from `CLAUDE.md` under Hard rules.