# 0009 — Phase 0 error hierarchy and partial structured-context fill

**Date:** 2026-05-18
**Phase:** Phase 0 (Task 4)
**Architecture refs:** `docs/coding-conventions.md §6.1`

## Decision

Task 4 creates `cyberlab_gen/errors.py` with the three error classes Phase 0 needs to raise:

- `CyberlabGenError(Exception)` — root; accepts `stage`, `run_id`, `cause` keyword arguments per `coding-conventions.md §6.1`.
- `RegistryError(CyberlabGenError)` — pins `stage="registry"`.
- `RegistryLoadError(RegistryError)` — adds `path: Path`; carries the underlying `YAMLError` / `pydantic.ValidationError` as `cause` and via `raise ... from`.

Other stage classes the convention names (`IngestionError`, `ExtractionError`, `PlanningError`, `GenerationError`, `ValidationLayerError`, `ProviderError`) are NOT stubbed; each lands in the task that first raises that error.

The `run_id` field is declared in the `CyberlabGenError.__init__` signature but always passes through as `None` in Phase 0 — there is no pipeline runner to source one yet. Phase 1's pipeline-runner task is responsible for wiring `run_id` through into every `raise` site.

## Context

`coding-conventions.md §6.1` (lines 232-237) says:

> The project defines its own exception hierarchy in `cyberlab_gen.errors`. Top-level: `CyberlabGenError(Exception)`. Subdivisions follow the architecture's stage boundaries: `IngestionError`, `ExtractionError`, `PlanningError`, `GenerationError`, `ValidationError`, `ProviderError`, `RegistryError`, etc. Each error class carries structured context (`stage`, `run_id`, `cause`).

Phase 0 has no Ingestion, Extraction, Planning, Generation, Provider, or Validation runner — Task 4's registry loader is the first stage with a real error to raise. Two questions:

1. Stub every stage subclass now, or only those that get raised in Phase 0?
2. How to handle `run_id` when there's no concept of a run yet?

## Alternatives considered

- **Stub all stage subclasses now.** Rejected (user decision during plan review). Every subsequent task would re-touch `errors.py` to flesh out its stage class anyway; pre-stubbing risks the stubs drifting out of sync with the actual error semantics each stage needs. Trimming `errors.py` to what's actually raised matches CLAUDE.md's "Don't add error handling, fallbacks, or validation for scenarios that can't happen."
- **Skip `run_id` from the signature until Phase 1.** Rejected. Adding a parameter later would silently change catch-and-read sites in Phase 1 code that doesn't know `run_id` was retrofitted. Declaring `run_id: str | None = None` in the Phase-0 constructor lets every caller rely on `.run_id` existing immediately.
- **Co-locate `RegistryLoadError` under `cyberlab_gen/registries/errors.py`.** Rejected. `coding-conventions.md §6.1` explicitly says "The project defines its own exception hierarchy in `cyberlab_gen.errors`." Splitting it per subpackage would force every consumer to know where each error lives — exactly the pattern the convention rejects.
- **Three classes in one file, run_id declared but None, other stage classes added per future task.** Chosen.

## Consequences

- Phase 0 ships `cyberlab_gen/errors.py` with three classes. The next task that raises a non-registry error adds its own stage subclass.
- `RegistryLoadError` instances always have `stage="registry"`, a populated `path: Path`, and `run_id is None`. The `cause` attribute mirrors `__cause__` for consumers that read attributes.
- Test `test_registry_load_error_carries_path_attribute` pins the structured-context contract.
- Phase 1's pipeline-runner task must (a) source a `run_id` for the run and (b) thread it into every `raise RegistryLoadError(...)` site (currently 5 in `cyberlab_gen/registries/loader.py`). One Phase-1 follow-up: wrap the loader entry-points in a thin run-aware adapter that injects `run_id`, so individual call sites don't all change.

## Supersedes

None.
