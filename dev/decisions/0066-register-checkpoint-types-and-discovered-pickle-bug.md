# 0066 — Register framework checkpoint types with the serializer (+ a discovered latent pickle bug)

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — checkpoint serde registration)
**Architecture refs:** ADR 0040 (checkpointer + the `pickle_fallback` serializer), ADR 0053 (the run
store reads the checkpoint as the single persistence authority), ADR 0023 (the locked checkpointer
surface). Confined to `framework/checkpointing.py` (the single serializer construction point).

## Decision (the registration)

Every run logged `"Deserializing unregistered type … will be blocked in a future version"` for ~9–11
framework state types. `open_sqlite_checkpointer` now constructs
`JsonPlusSerializer(pickle_fallback=True, allowed_msgpack_modules=_REGISTERED_CHECKPOINT_TYPES)`, where
`_REGISTERED_CHECKPOINT_TYPES` lists the framework `PipelineState`-channel types as `(module, name)`
**string tuples** (verified against the installed langgraph: `_check_allowed` matches `(module, name)`
keys; entries may be tuples or classes). The registered set (11) is:

`StaticSchemaResult`, `StaticSchemaCode` (validators.static_schema_validator); `GroundingResult`,
`GroundingCode` (validators.grounding_validator — **new from this batch's A3/B1**); `Verdict`,
`JuryVerdict` (extractor_jury.schema); `EnrichmentResult` (framework.enrichment); `PipelineStatus`,
`FeedbackKind`, `RefinementFeedback`, `PipelineState` (framework.orchestrator).

- **String tuples, not imported classes** — `checkpointing.py` deliberately keeps its lazy import graph
  (it never imports orchestrator/validators/jury/enrichment at module scope); the serializer's
  ext_hook `import_module`s them on read.
- **Nested models** (`StaticSchemaFinding`, `GroundingFinding`, `JuryFieldFeedback`, `JuryScores`) ride
  as plain dicts inside their parent's `model_dump` payload → no separate ext envelope → not listed.
- **`pickle_fallback=True` stays** for the `HttpUrl`-bearing `AttackSpec`/`ExtractionResult` subtree
  (ADR 0040 security note: local, code-created checkpoints only); it is independent of the allowlist.
- **Maintenance obligation:** an explicit allowlist flips the serializer out of permissive
  "allow-all-with-warning" into a finite allowlist that **blocks** unlisted types (returns raw data,
  failing resume). Any new msgpack-serialized top-level `PipelineState` channel type added later MUST be
  added here. The no-warning round-trip tests (clean / structural-retry / grounding-retry) in
  `test_checkpointing.py` guard both no-unregistered-warning and no-collateral-block.

## Discovered (NOT fixed here): a latent run-crashing pickle bug

Writing the grounding-retry round-trip test surfaced a **pre-existing** bug, independent of this item:
an ad-hoc **`Provenance[<custom enum>]`** is not picklable. Concretely, `CveReference.severity` is typed
`Provenance[Severity] | None`, and enrichment's `_rewrite_severity` constructs `Provenance[Severity]`.
When the checkpointer falls back to `pickle` for the `HttpUrl`-bearing spec subtree, pickling a
`Provenance[Severity]` instance raises
`PicklingError: Can't pickle <class 'Provenance[Severity]'>`. The aliased parametrizations
(`ProvenanceString = Provenance[str]`, `ProvenanceFloat = Provenance[float]`, …) pickle fine — the
difference is **builtin type args (`str`/`float`) pickle by reference in the Pydantic-v2 generic reducer,
while a custom-enum arg does not** (confirmed empirically; a module-level alias does **not** fix it).

**Impact:** a real `--auto` run that extracts (or enriches) a CVE with a populated `severity` field would
**crash the checkpointer** (the run-store enables checkpointing on every run). The codebuild + sysdig
proof runs shipped because their specs had no `cvss_score`/`severity`-as-`Provenance[Severity]` field
populated (CI regex-bypass / LLMjacking are not CVE-severity-driven). The bug is latent, not regressed by
this batch — it surfaced only because the serde test deliberately built a CVE-bearing spec.

**Decision: defer, do not fix in this batch.** It is out of the serde item's scope (serde *warning*
registration) and the listed 8-item batch, and the fix is non-trivial — a custom `__reduce__` on
`Provenance` (reconstruct via origin + args), retyping `severity` to a picklable shape, or making the
checkpoint serializer `model_dump` the provenance subtree instead of pickling it. It belongs on the
Phase-1.5/2 deferred list. The serde test uses the picklable `cvss_score`/`ProvenanceFloat` external_api
field to validate registration without conflating the two issues.

## Consequences

- No more unregistered-type warnings; the 11 framework types round-trip via the registered path; resume
  (ADR 0053) is unaffected. `pickle_fallback` safety (ADR 0040) preserved.
- **Flagged to the maintainer for the eval re-run:** a blog whose extraction populates a CVE `severity`
  (`Provenance[Severity]`) will crash the checkpointer until the deferred pickle fix lands. Prefer a blog
  without CVE-severity content for the consolidation re-confirmation run, or apply the fix first.
