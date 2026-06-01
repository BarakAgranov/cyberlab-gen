# 0026 — Validator modules/classes use descriptive names; numbered report keys retained

**Date:** 2026-06-01
**Phase:** 1 (post-Task-6 architect pass)
**Architecture refs:** `validation.md §6.3` (report shape is a stable contract), `validation.md §6.4`, `coding-conventions.md §3.1`, ADR 0022 (amended)

## Decision

The validator's **source files and Python symbols** use self-documenting descriptive names, not numbered `layerN` names. Concretely, the Phase-1 Layer 1 module is renamed:

- `cyberlab_gen/validators/layer1.py` → `cyberlab_gen/validators/static_schema_validator.py`
- `Layer1Validator` → `StaticSchemaValidator`
- `Layer1Result` → `StaticSchemaResult`, `Layer1Finding` → `StaticSchemaFinding`
- `Layer1Code` → `StaticSchemaCode` (renamed for consistency with its three sibling symbols, so the module has no half-renamed `Layer1*` token; see "Scope note" below)

The class docstring keeps its `"Validator Layer 1 — static schema validation + registry reference resolution"` opening line, so the file states **both** its descriptive role and its report-layer number.

**Numbered identifiers are retained where the number is the contract or a stable key**, per `validation.md §6.3`:

- Any serialized validation-report key stays numbered (`layer_1`).
- The pipeline state slot `state.layer1`, the orchestrator node name `"validate_layer1"`, and the eval metrics `layer1_passed` / `layer1_pass_rate` / `overall_layer1_pass_rate()` are retained unchanged — they are report/metric keys and graph identifiers, not the validator's source symbols.

This **amends ADR 0022**, which named the module `layer1.py` and the class `Layer1Validator`. ADR 0022's *location* decision (a dedicated top-level `cyberlab_gen/validators/` subpackage, one module per layer) stands; only the numbered-name convention is rejected.

## Context

ADR 0022 shipped `layer1.py` / `Layer1Validator`. The numbered name is opaque at the call site — `Layer1Validator(...)` says nothing about *what* it validates. The validator's layers each have a distinct, nameable job (`validation.md §6.4`–§6.8: static schema, semantic cross-check, containerized dry-run, safety scans), so a descriptive name (`StaticSchemaValidator`) is strictly more informative and reads better in the orchestrator and CLI wiring. At the same time, `validation.md §6.3` makes the report *structure* — including the per-layer keys — a stable contract, and the eval harness aggregates on `layer1_*` metric names. Renaming those keys would be a gratuitous contract break. The split is therefore: descriptive **symbols**, numbered **keys**.

## Alternatives considered

- **Keep numbered names (status quo / ADR 0022 as written)** — rejected: opaque at call sites; the whole point of the pass is self-documenting clarity.
- **Rename everything including report/metric keys** — rejected: breaks the `validation.md §6.3` report-shape contract and the eval metric names for no benefit; the layer *number* is a real, stable identifier of where a finding sits in the cheap-to-expensive layer order.
- **Leave `Layer1Code` numbered while renaming the other three** — rejected: leaves a half-renamed module (`StaticSchemaValidator` emitting `Layer1Code` findings), which is exactly the opaque inconsistency this pass removes.

## Consequences

- Layers 2/3/5 (Phase 2+) land beside this module with descriptive names (e.g. `semantic_crosscheck_validator.py`, `containerized_dryrun_validator.py`, `safety_scan_validator.py`), each keeping its numbered report key.
- All importers updated: `framework/orchestrator.py`, `cli/extract.py`, `cli/main.py`, `eval/runner/cli.py`, `eval/runner/runner.py`, and the two tests (`tests/unit/validators/test_static_schema_validator.py` — also renamed — and `tests/unit/framework/test_orchestrator.py`). `just verify` stays green (492 tests).
- **Scope note:** renaming `Layer1Code` → `StaticSchemaCode` was not one of the three symbols enumerated in the task brief, but leaving it would have produced a half-renamed module; it is renamed for consistency and flagged here per CLAUDE.md (no silent scope decisions).
- The `CLAUDE.md` project map gains the `validators/` line (the maintainer doc-edit fix that ADR 0022 flagged), naming `static_schema_validator.py` as the Phase-1 module.
