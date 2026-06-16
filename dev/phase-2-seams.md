# Phase-2 seams & tracked deferrals

**Purpose.** A single durable home for the structural seams Phase 2 must introduce (at the real
second use — **do not pre-build**), plus the contract-safe deferrals and opportunistic cleanups that
the pre-Phase-2 fix register (investigations `0003` + `0004`) surfaced and intentionally did **not**
do. Each line is scoped so it cannot rot.

Provenance: derived from `dev/investigations/0003-deferral-and-integrity-audit.md` and
`dev/investigations/0004-design-quality-review.md`. The pre-Phase-2 correctness/contract work
(Batches A + B) landed as ADRs 0067–0075; what remains below is **tracked, not built**.

---

## 1. Tier ③ — structural seams Phase 2 must introduce (design at second-use)

These are real and correctly identified, but building them before Phase 2's concrete second use
risks the wrong abstraction. Introduce each at the moment the second instance lands.

### ③.1 — `Stage`/`Node` abstraction + reducer channels  (0004 §1.4 S1+S2)
`orchestrator.build_pipeline` is a ~390-line closure factory that *is* the state machine: nested
closures, a hand-maintained `_Node` `StrEnum`, hand-wired `add_node`/`add_conditional_edges` with
repeated destination-map literals, no compile-time sync between enum/registration/maps (a missing key
is a runtime `KeyError`). And `PipelineState` is a single linear in-place-mutated channel with **no
LangGraph reducers** — Phase-2 parallel generators have no merge mechanism (last-write-wins / error).
**Requirement:** a registered `{name, work_fn, routing_fn}` `Stage`/`Node` table the builder iterates
(edge maps derived from a typed enum) **and** `Annotated`-reducer channels — *before the first
parallel node lands*. (Also subsumes the Tier-4 `route_with_budget(...)` primitive and the per-node
instrumentation-wrapper item.)

### ③.2 — Data-driven enrichment (registry `enrichment_triggers` → per-source adapters)  (0004 §1.4 S13)
`framework.enrichment.enrich()` dispatches by hardcoded `_NVD_SOURCE_ID`/`_MITRE_SOURCE_ID` into
bespoke `_enrich_*`/`_parse_nvd_response`, looping the rest only to emit stub skips — while the
registry already models the full contract (`EnrichmentTrigger{field, action, endpoint}`,
`ExternalSourceEndpoint{…, response_schema_ref}`, adapter module under
`cyberlab_gen/external_data_sources/<id>/`). **Requirement:** drive enrichment from declared
`enrichment_triggers`, resolve a per-source adapter; NVD is the first adapter behind the seam. This
is the landing point for the **ADR 0077** external-source work-stream (the inert CVE ship-gate, the
`source_of_record` check, the `advisory.source` retype).

### ③.3 — Provider factory / dispatch (multi-provider)  (0004 §1.4 S18)
`ProviderRegistry.resolve()` returns `(provider, model)` but **no consumer dispatches on
`provider`** — the CLI builds one `CostRecordingProvider(AnthropicProvider())` for every agent.
`model_rankings.yaml` lists OpenAI placeholders, so multi-provider is intended. **Requirement:** a
provider factory/dispatch keyed on the resolved provider name. The ADR-0071 single-resolution fix is
the *correctness* prerequisite (done); this is the *extensibility* build on top. (Also splits the
single `TokenUsage.cache_write_tokens` into per-tier fields when a second provider bills cache
differently, and lifts the Anthropic-specific request config out of the shared cost layer.)

### ③.4 — Run-correlation telemetry spine + persist the loop trajectory  (0004 §1.4 S53)
Observability is Extractor-shaped: rich routing telemetry is computed then **dropped on
persistence**, so a failed *multi-agent* run can't be reconstructed; spans are bare; no shared
`run_id` across logs / spans / ledger. **Requirement:** give logs/spans/ledger a shared `run_id`
and persist the loop trajectory so a multi-agent run is reconstructible. (Pairs with the in-loop
budget interrupt, ADR 0063.)

### ③.5 — Loop-budget threading  (already ADR 0063 — the gold-standard work-stream)
The in-loop budget interrupt + the three stopgaps it removes. Already captured; listed here only so
the seam set is complete. No new tracking needed.

---

## 2. Tier ④ — opportunistic cleanups (tracked; not folded into Batches A/B)

Done during Batches A/B (folded in): the `agents↔framework` cycle (ADR 0075), `MergedRegistries`
typing (ADR 0072), finding-locator integer-index canonicalisation (ADR 0074). Closed just after
Batches A/B: the **verify-only jury tool set (ADR 0078)** — a mechanically-enforced verify-only tool
set on the `ToolUsingAgent` contract (withheld `propose_*` defs + executor guard); the Extractor-Jury
is fixed, and Phase-2's Planner-Jury (brief Task 4) and Phase-4's Critic inherit the enforcement as
they are built. The rest, tracked:

- **CLI reaches into orchestrator privates.** `cli/extract.py` imports `_ingestion_summary`
  (`# pyright: ignore[reportPrivateUsage]`) and `_state_to_run_result` re-derives the `HALTED_*`
  terminal-state→result mapping that `orchestrator._finalize` owns (the duplication ADR 0067's
  CLI-path fix had to patch in two places). **Fix:** widen `PipelineOutcome` to carry proposals +
  cost basis; one home for terminal-state→result.
- **Two parallel status taxonomies.** `PipelineStatus.SHIPPED_LOW_CONFIDENCE="shipped_low_jury_confidence"`
  vs `RunStatus.SHIPPED_LOW_CONFIDENCE="shipped_low_confidence"`; bridged lossily across CLI + eval
  (the eval mapper lacks `INTERRUPTED`/`CRASHED`). **Fix:** one shared mapping. (Would also let the
  Tier-1 ②.2 shared persistence service own status, the "fuller service" option deferred there.)
- **`ExtractRunner` Protocol omits the stateful read-back surface.** Persistence reaches
  `last_state`/`content_hash` via `getattr(...)`-with-None + `isinstance` narrowing — a rename
  silently degrades every persistence path to "no partial spec saved" with no type error. **Fix:**
  promote `last_state`/`content_hash` to the typed `ExtractRunner` contract.
- **`NvdClient` Protocol lives in `framework.enrichment`** but agents + validators import it. **Fix:**
  move to a neutral ports module. (Do with ③.2 prep.)
- **`OverlayRegistryFile._entry_key` reads `ENTRY_KEY_FIELD` via `getattr`** on a `BaseModel`-bounded
  generic — a Phase-2 entry type forgetting the ClassVar fails at runtime, not under pyright. **Fix:**
  a `KeyedRegistryEntry` Protocol bound.
- **No mechanical dedup of proposals against the merged registry before acceptance** — a proposal
  colliding with a *bundled* entry silently shadows it (overlay-wins). The only guard is the
  prompt-level digest. **Fix:** a mechanical "already registered?" accept-time check (matters more in
  Phase 2 with the Planner as a second proposer).
- **The seven-registry shape is hand-replicated across ~10 sites** (loader/merge/test). **Fix:** a
  `RegistryDescriptor` table iterated by load/merge so "add a registry" is one row. (Phase 2 adds
  many registries — could fold into ③.)
- **Proposal acceptance is parallel-by-hand per type** (three near-identical `accept_*` + copy-pasted
  loops, mirrored in the CLI). **Fix:** a `Proposal` protocol → one generic accept. (Planner = a
  fourth branch otherwise.)
- **Proposal-authority machinery hardcoded to `"extractor"`** (`proposed_by='extractor'` literals;
  `EXTRACTOR_FACET_CATEGORIES` gate; `ProposedFacet.category` literal). The Planner's runtime facets
  are structurally rejected at construction. **Fix:** stamp `proposed_by` at the framework accept
  boundary; make the authority gate a per-agent input.
- **In-process `MergedRegistries` snapshot goes stale after an overlay write** — benign today (only
  the Extractor proposes, write is after-ship); in Phase 2 the Planner reads the captured-once object
  and won't see just-accepted entries. **Fix:** re-read / invalidate after an overlay write. (Pairs
  with ③.1.)
- **No streaming / sectioned-emit seam in the locked call surface.** Both ABC methods return a
  fully-materialised `ProviderResponse`; ADR 0032/0033's durable truncation fix is streaming +
  sectioned emit — inexpressible by a single terminal-response method. The 20K `max_tokens` is the
  current stopgap. **Fix:** decide the streaming/sectioned-emit contract before the emit work.
- **`AttackSpec`/`LabManifest` share no envelope base.** Deferred from ①.3 (ADR 0069) by decision:
  extract a thin `SpecEnvelope` base (`spec_version`/`spec_kind`/`source` + the version machinery)
  at Phase-2's second use (`LabManifest`), so the load gate dispatches on `spec_kind`.
- **Manifest-side framework stamping** (ADR 0086). When the Planner's `LabManifest` ships,
  `GenerationBlock.model` must be stamped from the billed cost ledger (the ADR-0065 billed-model
  invariant, never the LLM's self-report) and `spec_version` stamped via the already-generic
  `stamp_spec_version`. **Generalize the one stamp home to dispatch on artifact type — do NOT add a
  third copy** (ADR 0068). Reachable only when the Planner persists a manifest (Task 6+); recorded
  now because the framework-owned-field audit (ADR 0086) made the field-home gap explicit.
- **`StepBlock.reproducibility` carry-integrity check** (ADR 0081 / 0086). When the Planner
  populates the per-step tier (carried forward unchanged from the source `ChainStep`), add a
  Layer-2 check asserting `StepBlock.reproducibility == source ChainStep.reproducibility`; decide
  whether an explicit `StepBlock → chain_step` back-ref is needed then (it depends on whether the
  Per-phase Generator needs an explicit step→tier mapping). Phase-2 Task-3 call.

---

## 3. Tier ②.4 — thin-tracked, contract-safe deferrals (scoped lines)

Sound today; recorded here so each is un-rottable (no engineering now). From investigation `0003
§1.2` (3-B…3-G):

- **No input-side chunking for long blogs.** `pipeline.md`/`agents.md` mandate input
  chunk-and-reconcile; `ingestion.py` caches full text and `extract()` is single-pass. An oversized
  input HALTS (ships nothing) — a coverage gap, not a correctness bug. (Distinct from the OUTPUT-emit
  streaming class-fix, ADR 0032/0033 / Tier-4 streaming-seam item.)
- **Run-report internal traces half-deferred.** `coding-conventions.md §6.3` ("internal traces always
  written to the structured report") is deferred (ADR 0013); only Phoenix spans are wired.
  Outcomes/cost/artifacts persist (ADR 0039/0053) — only the human-facing trace *section* is missing.
- **`run_id` not threaded into the registry loader.** All `RegistryLoadError` raise sites omit
  `run_id`; diagnostic ergonomics only (load aborts before the Extractor; `run_id` is never read
  back). (Subsumed by the ③.4 shared-`run_id` spine.)
- **`schema-details.md` `BaseModel`→`ArtifactModel` doc sweep.** ~40 `BaseModel(extra="forbid")`
  classes in the doc are not swept to `ArtifactModel`; the **code already uses `ArtifactModel`** (ADR
  0004) — docs-vs-code drift only. **Still tracked** (not done in the Task-0 reconciliation): ADR 0004
  explicitly rejected a blanket sweep (§6 / `MergedRegistries` need per-class code checks — some are
  `InternalModel`), so it stays incremental-per-transcription.
- ~~**`material_discrepancies` doc mirror pending.**~~ **DONE** — `schema-details.md §4.9` already
  carries the `MaterialDiscrepancy` block + the `AttackSpec.material_discrepancies` field (confirmed
  in the Phase-2 Task-0 reconciliation, 2026-06-16).
- **Anthropic live cassette pending.** ADR 0027's "real API call succeeds" exit criterion is PENDING;
  the live test skips, the cassette dir is absent. Regression-confidence only (the adapter is fully
  unit-tested offline). Record a real cassette-recording work item.
