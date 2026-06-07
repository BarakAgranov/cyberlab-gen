# 0046 — `static_schema` everywhere: rename the last `layer1` tokens (supersedes ADR 0026's keep-keys; amends ADR 0023)

**Date:** 2026-06-07
**Phase:** 1 (operational hardening)
**Architecture refs:** `validation.md §6.3` (report-shape contract), §6.4. **Supersedes
ADR 0026's** "numbered report keys / state slot / node id are retained" decision.
**Amends ADR 0023's** locked LangGraph node-id surface (the node id is renamed).

## Context

ADR 0026 renamed the validator's *source symbols* to `StaticSchema*` but deliberately
**kept** the numbered `layer1` token wherever it was a stable key or identifier: the
serialized report/metric keys (`layer1_passed`, `layer1_pass_rate`,
`overall_layer1_pass_rate`), the `PipelineState.layer1` slot, and the LangGraph node id
`"validate_layer1"`. The result was a half-numbered surface — descriptive class names
emitting `layer1_*` metrics into a `state.layer1` slot from a `validate_layer1` node —
plus `layer1` leaking into user-facing halt strings and logs.

The maintainer's decision (this session's sign-off): adopt the descriptive name
**everywhere**; the one-time churn (and the archived-report key break) is worth removing
the split. This explicitly overrides ADR 0026's keep-numbered-keys decision.

## Decision

Rename `layer1` → `static_schema` across **all** code, identifiers, report/metric keys,
user-facing strings, logs, and code docstrings/comments. No `layer1` token survives
anywhere in `cyberlab_gen/`, `eval/`, or `tests/`. Specifically:

- **Report/metric keys:** `layer1_passed` → `static_schema_passed`, `layer1_pass_rate`
  → `static_schema_pass_rate`, `overall_layer1_pass_rate()` →
  `overall_static_schema_pass_rate()` (`metrics.py`, `report.py`, `runner.py`,
  `progress.py`, eval CLI, and the tests/fixtures that reference them).
- **Pipeline state + node id (amends ADR 0023):** `PipelineState.layer1` →
  `PipelineState.static_schema`; the node id `"validate_layer1"` →
  `"validate_static_schema"`. The node-id surface was locked by ADR 0023; this is a
  deliberate, recorded amendment — no behaviour change, only the identifier.
- **User-facing strings / logs:** the halt reason and `ValidationError` messages now read
  "Static schema validation still failing / failed past the retry budget"; the routing /
  failure logs read "static schema validation …".
- **Code docstrings/comments:** prose "Validator Layer 1" / "Layer 1" referring to *this*
  pass now reads "static schema validation" / "the static schema validator".

## Scope boundaries (deliberately not renamed)

- **Archived eval reports** (`eval/reports/*.yaml`) keep their old `layer1_*` keys — the
  maintainer accepted that old reports are historical and not migrated. Consequence: an
  old archived report does not round-trip into the renamed `BlogRunRecord`/`BlogAggregate`
  models; this is accepted.
- **`docs/*.md` architecture taxonomy** keeps "Layer 1 / Layer 2 / Layer 3 / Layer 5" as
  the *positional* name of the layered-validation model (`validation.md`). Renaming the
  positional taxonomy there would orphan Layers 2/3/5 and rewrite the contract; per
  CLAUDE.md docs are not edited from an implementation task beyond the explicit Part-D
  fixes. The code symbol for the first pass is `StaticSchemaValidator`; the doc concept
  it implements is still "Layer 1" of the validation model. A maintainer doc pass may
  align the docs' report-key examples (`layer_1`) with the new keys.
- **Historical ADRs** (0016/0022/0026) keep their original `layer1` wording as a record.

## Consequences

- The validator surface is uniformly descriptive: `StaticSchemaValidator` emits
  `static_schema_*` metrics into `state.static_schema` from the `validate_static_schema`
  node, and halts/logs say "static schema validation".
- ADR 0026's symbol-rename stands; only its keep-numbered-keys clause is superseded.
- ADR 0023's node-id lock is amended (node id only); all routing behaviour is unchanged.
