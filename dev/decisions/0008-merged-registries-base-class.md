# 0008 — `MergedRegistries` base class and config

**Date:** 2026-05-18
**Phase:** Phase 0 (Task 4)
**Architecture refs:** `docs/schema-details.md §6.6`, `docs/architecture.md §1.5`, `dev/decisions/0004-base-class-discipline.md`

## Decision

`MergedRegistries` (in `cyberlab_gen/registries/merge.py`) inherits from Pydantic `BaseModel` directly under ADR 0004 reserved case 3 (ad-hoc internal type that never serializes). Its `model_config` is `ConfigDict(frozen=True, extra="forbid")` — `arbitrary_types_allowed=True` from the doc is dropped.

## Context

`docs/schema-details.md §6.6` shows:

```python
class MergedRegistries(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    value_types: ValueTypesRegistry
    ...
```

Three questions surfaced during Task 4 planning:

1. Per ADR 0004, `BaseModel` is reserved for project base classes, generic bounds, and ad-hoc internal types that never serialize. Which case does `MergedRegistries` fit?
2. Is `arbitrary_types_allowed=True` actually justified?
3. Should the class be frozen?

## Alternatives considered

- **`ArtifactModel`.** Rejected: artifacts are YAML-serialized boundary objects. `MergedRegistries` is a runtime view, never round-trips through YAML, never crosses the artifact boundary. Forcing it through the artifact base would add YAML-round-trip surface (`to_yaml`, `from_yaml`) the class doesn't need or use.
- **`InternalModel` (`extra="ignore"`, `validate_assignment=False`).** Rejected: `extra="ignore"` would silently swallow field-name typos at the merge call site (`MergedRegistries(valuetypes=..., ...)` would construct silently with `value_types` left as the default). The Phase-0 merge module is the only construction site, so typos would not be subtle; we want them caught.
- **`BaseModel` per ADR 0004 case 3, `frozen=True`, `extra="forbid"`, drop `arbitrary_types_allowed`.** Chosen.
- **Keep `arbitrary_types_allowed=True`.** Rejected: every field of `MergedRegistries` is a Pydantic `ArtifactModel` subtype (`ValueTypesRegistry`, `FacetsRegistry`, etc.). The setting is dead weight; per CLAUDE.md "Don't add features, refactor, or introduce abstractions beyond what the task requires." This matches ADR 0004's incremental-doc-cleanup pattern — the doc gets corrected when the section is next exercised.

## Consequences

- `MergedRegistries` is immutable post-construction. `architecture.md §1.5` ("LLMs never modify shared state outside their designated output") gains a mechanical enforcement point — downstream stages cannot patch entries mid-run.
- `extra="forbid"` catches construction-site typos at runtime.
- The empirical check (recorded in the execution log) confirmed Pydantic v2 allows populating `PrivateAttr` fields from `model_validator(mode="after")` even when `frozen=True`. The `_build_indices` validator builds the per-accessor lookup dicts there.
- `docs/schema-details.md §6.6` line 1358 continues to show `BaseModel(arbitrary_types_allowed=True)`. Flagged as a doc-improvement note in the Task 4 execution log; the doc gets updated incrementally per ADR 0004's policy, not as part of Task 4.

## Supersedes

None.
