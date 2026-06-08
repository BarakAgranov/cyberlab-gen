# 0058 — Implement the external-source / MITRE category-error fixes (the NOW batch)

**Date:** 2026-06-08
**Phase:** 1 (the sign-off-gated implementation of the ADR-0055 principle)
**Principle / investigation:** [ADR 0055](0055-external-sources-are-tools-not-registries.md)
(external sources are tool adapters, not proposable registries; unverifiable ≠ invalid) and
`dev/investigations/0001-external-sources-and-convergence.md` (the run reconstruction, the two
category errors, the NOW/LATER split).
**Amends in practice:** the as-built `_check_mitre` hard-reject, `_check_external_sources`,
`_enrich_techniques`'s seed-gate, the Extractor prompt + `external_lookup` tool description, and
the docs that mis-framed `external_data_sources` as a proposable vocabulary. Annotates ADR 0044
and ADR 0050.
**Architecture refs:** `architecture.md §1.5`/`§1.6` (LLM/framework split; mechanical checks),
`validation.md §6.4`/`§6.10` (the static-schema contract — what counts as a blocking finding),
`schema.md §4.14` (the keeper: external_data_sources vs static_catalogs as tool catalogs).

## Context

A real `--auto extract` on the Wiz CodeBuild blog spent $7.40 over 6 LLM calls and never
shipped. ADR 0055 settled the principle; this ADR records the **NOW** implementation that turns
it into code (the LATER adapter builds — NVD client, a MITRE `lookup_by_id`/`lookup_by_description`
adapter, OSV — remain deferred, findings doc 0001 §5). The change touches the validation
contract, so it was proposed and signed off before implementation.

## Decision (what changed)

1. **`_check_mitre` is ungated** (`agents/extractor/extractor.py`). The 8-entry seed-catalog
   membership gate is removed. The check now mirrors `_check_cves`'s skip-when-unwired posture:
   it produces **no findings**, logging which technique ids went unverified. The dead
   `mitre_catalog` constructor param + catalog import were removed (no caller passed them).
2. **`_check_external_sources` is removed** (`validators/static_schema_validator.py`), both
   branches: `advisory.source` (a publisher provenance label, the lone unconvergeable
   ship-blocker) and `cve.source_of_record` (framework-authored by enrichment *after* this gate;
   the Extractor leaves it `None`). The `UNKNOWN_EXTERNAL_SOURCE` code is retained, reserved for
   a deferred post-enrichment `source_of_record` verification.
3. **`_enrich_techniques` no longer treats an uncatalogued id as a discrepancy**
   (`framework/enrichment.py`). A well-formed id absent from the seed is recorded as an honest
   **unverified skip** (`SkippedLookup`), never a false "contradicting technique"
   `MaterialDiscrepancy` (item 1b — keeps the run report clean).
4. **Prompt + tool description corrected** (`agents/extractor/prompt.md`,
   `agents/extractor/tools.py`). Search-before-claim no longer mandates looking up MITRE ids,
   GitHub repos, or packages (no adapter serves them); the model is told to cite blog-named
   technique ids and mark unverifiable ones appropriately, and the "rejected as hallucination"
   threat for MITRE is gone.
5. **Docs/ADRs de-conflated** `external_data_sources` from the proposable-vocabulary framing
   (`validation.md §6.4`, `schema.md §4.16` summary), re-classed the MITRE seed as
   external-authority data (`loader.py`, the seed YAML header, `registry-details.md`,
   `pipeline.md`), and annotated ADR 0044 / ADR 0050.

## Three corrections to the findings doc (recorded so they are not re-litigated)

- **Well-formedness is type-owned, so there is no regex re-check in `_check_mitre`.** Both
  technique-ref fields are `MitreTechniqueId`-typed (`ChainStepTechniques.mitre`,
  `MitreTechniqueReference.technique_id`), enforced at AttackSpec construction
  (`primitives.py`). A malformed id can never reach `_check_mitre`; the "keep only the
  well-formedness check" recommendation is satisfied one layer up, by the type. The
  "malformed id still fails" guarantee is tested at construction, not in the framework check.
- **There is no soft "unverified finding" shape — the status is model-authored provenance.**
  Every `CheckFinding` in the Extractor's `_run_checks` is blocking (re-prompt → `ExtractionError`),
  so a well-formed-but-unverifiable id must produce **no** finding. The "requires external
  research / unverified" status lives in the field's provenance (prompt-driven), exactly as a
  CVE skipped by `_check_cves` (no NVD client) already works. The framework's only job is to
  stop rejecting.
- **`tools.py` was corrected NOW, not LATER.** The `external_lookup` description asserted
  "technique ids are validated automatically against the bundled MITRE catalog" — false after
  the ungate, and it would re-induce the drop-the-technique behavior. A minimal truth-correction
  was made now; advertising a real `mitre_attack` lookup source stays LATER with the adapter.

## The validation contract delta (the gated part)

| Finding (was blocking) | Layer | Trigger before | After |
|---|---|---|---|
| `mitre_hallucination` | Extractor `_run_checks` → `ExtractionError` | technique id ∉ 8-entry seed | unproducible for well-formed ids; passes through |
| `UNKNOWN_EXTERNAL_SOURCE` @ `advisory.source` | static-schema gate → halt | not in `['nvd']` registry | check removed |
| `UNKNOWN_EXTERNAL_SOURCE` @ `cve.source_of_record` | static-schema gate | non-None & not in registry | check removed (latent today; enrichment authors a valid id) |

Unchanged — still blocks: `SCHEMA_INVALID`, `SPEC_KIND_MISMATCH`, `UNKNOWN_FACET` /
`UNKNOWN_THESIS_TYPE` (unless provisionally resolved by an in-flight proposal), `CATALOG_DRIFT`,
`search_before_claim`, `cve_hallucination` (when an NVD client is wired), and malformed CVE/MITRE
ids (rejected at construction by their types). The grounding backstop for a fabricated-but-well-
formed technique id moves from the broken seed to the jury's fidelity review and the LATER MITRE
adapter — the same posture a fabricated CVE already has when no NVD client is wired.

## Reconciliations recorded

- **Loaded vs documented external sources.** The bundled `registry/external_data_sources.yaml`
  loads **only `nvd`**. `registry-details.md` documents `mitre_attack` and `osv` entries too;
  those are **aspirational** (the intended catalog). In the as-built Phase 1, `mitre_attack` is a
  bundled local seed (`registry/mitre_attack_techniques.yaml`, not a registry entry / adapter)
  and `osv` is unbuilt. No external-source adapter code exists for any source yet.
- **MITRE is not an ADR-0016 closed catalog.** ADR 0016's closed catalogs are project-owned
  enums; the MITRE seed is external-authority data, a Phase-1 stopgap. `loader.py`'s grouping was
  corrected.

## Deferred (LATER — explicitly NOT in this batch)

- Wiring real adapters under `cyberlab_gen/external_data_sources/`: NVD client; a MITRE ATT&CK
  adapter with `lookup_by_id` + `lookup_by_description` (so the Extractor can *fetch* the right
  technique when the blog describes but does not name one); OSV.
- Retyping `AdvisoryReference.source` off `ExternalDataSourceId` to a publisher label/enum (kept
  typed-but-unchecked now; nothing in Phase 1 reads it as a tool id). An inline NOTE on the field
  and this ADR guard against a future reader re-adding the check.
- A post-enrichment verification of `cve.source_of_record` (re-homing the `UNKNOWN_EXTERNAL_SOURCE`
  check there).
- The held consolidation items (A3/B1, C1, E1, B2), deliberately kept out so a failure stays
  bisectable.

## Caveat for the proof run — the `--auto` proposal cap (NOT a category-error regression)

These fixes remove the category-error ship-blockers, but they do **not** touch the per-run
auto-accept proposal cap (`DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP = 5`, `cli/extract.py`). The
reconstructed run emitted ~8 registry proposals; under `--auto`, >5 proposals **halts** with
`ProposalCapExceeded` (ADR 0044) — a deliberate gate, not a regression. The cap's real fix
(in-loop steering bounded by the refinement caps) is ADR 0050's later work-stream, held for the
consolidation run. **To raise/bypass the cap for the proof run** so it doesn't mask whether these
fixes worked, either run `--interactive` (no cap — the user acts on each proposal), or raise
`DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP` in `cyberlab_gen/cli/extract.py` for the run. There is no CLI
flag for it today.

## Report: `run_pipeline` / `_finalize` (no change made)

`run_pipeline` (`framework/orchestrator.py`) and its private `_finalize` are **vestigial and
test-only**: production drives `build_pipeline` directly via `PipelineExtractRunner._drive`
(`cli/extract.py`), mapping the terminal state with the module-private `_state_to_run_result`
(which reads `PipelineState.extraction` for the proposals `PipelineOutcome`/`_finalize` drop —
the reason ADR 0024 superseded them). `run_pipeline`/`_finalize` are referenced only by
`tests/unit/framework/test_orchestrator.py`. They remain a locked surface (ADR 0023 names
`build_pipeline` / `run_pipeline`; ADR 0040 amended the returned callable). Retiring them is a
locked-surface decision (an amendment ADR + deleting the symbols and re-homing the tests onto
`build_pipeline`), out of scope here — **left in place**, per the prior-batch decision.

## Consequences

- No change to `architecture.md §1.5`/`§1.6`: the gate stays mechanical, the agent still only
  produces content and decides whether to call a tool. This sharpens what "resolve into a
  registry" may and may not mean.
- Tests pin the contract: a well-formed-but-uncatalogued MITRE id passes unverified; a malformed
  id fails at construction; `advisory.source='aws'` and a bogus `cve.source_of_record` both ship;
  an uncatalogued technique is an enrichment skip, not a material discrepancy.
- `just verify` (ruff, ruff format, pyright strict, pytest) green per commit; per-item commits; no
  tag; no provider-backed eval run.
