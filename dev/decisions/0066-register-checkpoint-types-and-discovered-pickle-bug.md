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

## Discovered (deferred here; RESOLVED same-day — see Resolution below): a latent run-crashing pickle bug

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

**Decision (at the time): defer, do not fix in this batch.** It was out of the serde item's scope (serde
*warning* registration) and the listed 8-item batch, and the fix is non-trivial — a custom `__reduce__` on
`Provenance` (reconstruct via origin + args), retyping `severity` to a picklable shape, or making the
checkpoint serializer `model_dump` the provenance subtree instead of pickling it. The serde test uses the
picklable `cvss_score`/`ProvenanceFloat` external_api field to validate registration without conflating the
two issues. **This deferral was lifted the same day — see the Resolution section; the bug is now fixed.**

## Consequences

- No more unregistered-type warnings; the 11 framework types round-trip via the registered path; resume
  (ADR 0053) is unaffected. `pickle_fallback` safety (ADR 0040) preserved.
- **Flagged to the maintainer for the eval re-run (now resolved):** a blog whose extraction populates a
  CVE `severity` (`Provenance[Severity]`) used to crash the checkpointer. The Resolution below lands the
  fix, so a CVE-severity blog now round-trips cleanly; the re-run can deliberately target CVE-severity
  content to exercise the path the two proof blogs never hit.

## Resolution (2026-06-09): fixed at the root via `Provenance.__reduce__`

**Status: RESOLVED.** Implemented as a same-day follow-up. `just verify` green (ruff, ruff format, pyright
strict, pytest 696 passed / 1 skipped).

**Approach: ADR-sketch option 1 — a custom `__reduce__` on `Provenance` (reconstruct via generic origin +
args).** NOT the originally-preferred registered/msgpack path. The follow-up brief preferred extending the
registered (non-pickle) path that this batch used for the 11 framework types; that path is **not viable
here**, for a reason confirmed empirically:

- The `AttackSpec` channel **always carries `HttpUrl`** (`SourceBlock.url` / `canonical_url`), which
  `JsonPlusSerializer`'s `_msgpack_default` cannot encode. So `dumps_typed` raises `MsgpackEncodeError` and
  falls the **entire spec** to `pickle` (verified: a baseline spec — no CVE — serializes as serde type
  `pickle`). `Provenance` therefore **never reaches** the msgpack `EXT_PYDANTIC_V2` path, so adding it to
  `allowed_msgpack_modules` has zero effect. (`Provenance[Severity]` msgpacks fine *in isolation* — the
  problem is purely that the HttpUrl in the same channel forces wholesale pickling.) Extending the
  registered path would mean making the whole spec — HttpUrl included — msgpack-serializable, i.e.
  reopening ADR 0040's deliberate `pickle_fallback` choice; out of scope for a serialization bugfix.

**Precise root cause (refines this ADR's original "builtin vs custom-enum" note).** Pydantic registers a
parametrized generic in its module namespace — which is what makes pickle-by-reference (`__newobj__` +
class) succeed — **only when the parametrization is first created at module-global scope**
(`pydantic._internal._generics.create_generic_submodel` → `_get_caller_frame_info().called_globally`, which
does `setattr(module, 'Provenance[...]', cls)`). The builtin aliases (`ProvenanceString = Provenance[str]`,
…) execute at global scope, so `Provenance[str]/[float]/[int]/[bool]/[list[str]]` get registered and
pickle. `Provenance[Severity]` has no such alias and is first built **lazily inside pydantic's schema
construction** for `DetectionBlock` / `CveReference` (a non-global frame), then cached — so it is never
registered and `pickle` cannot look it up by reference. This is also exactly **why "a module-level alias
does not fix it"**: by the time any `ProvenanceSeverity = Provenance[Severity]` line would run, the
unregistered class is already in pydantic's generics cache, so the alias is a cache hit that never
re-triggers `create_generic_submodel`'s registration — fragile and import-order-dependent.

**The fix.** `Provenance.__reduce__` returns `(_rebuild_provenance, (type_args, self.__getstate__()))`,
where module-level `_rebuild_provenance` re-subscripts `Provenance[type_args[0]]` and restores state via
pydantic's own `__setstate__` (no re-validation). `_rebuild_provenance` is picklable by reference precisely
because the parametrized classes are not. This makes the **whole `Provenance[T]` family** round-trip through
pickle deterministically and independent of import order — covering `CveReference.severity` and
`DetectionBlock.severity` (the two `Provenance[Severity]` sites) and any future custom-typed
parametrization. `pickle_fallback` (ADR 0040) is unchanged; we make the type it relies on actually
picklable, which is the contract `pickle_fallback` already assumes. `copy`/`deepcopy` are unaffected
(pydantic's `__copy__`/`__deepcopy__` take precedence) and the msgpack `model_dump` path is untouched.

**Tests.** `tests/unit/schemas/test_provenance.py`: the whole family (`str`, `list[str]`, `float`, `int`,
`bool`, `Severity`) pickle-round-trips preserving concrete class + value; a focused custom-enum regression.
`tests/unit/framework/test_checkpointing.py`: a CVE-severity spec round-trips through the checkpoint
(write + `read_latest_pipeline_state`) with no serde warning, and a mid-graph abort still recovers the
partial CVE-severity spec from the checkpoint (the L4/G1 path, ADR 0053). All four fail without the fix
(the two builtin-family cases stay green — correct discrimination).
