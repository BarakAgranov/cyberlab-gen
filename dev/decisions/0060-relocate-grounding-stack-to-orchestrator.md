# 0060 — Implement the one orchestrator-owned grounding stack (the Extractor stops self-validating)

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — item **A3/B1**)
**Architecture refs:** `validation.md §6.10.1` (the four-redo taxonomy) + `§6.10.2` (one
orchestrator-owned mechanical-validator stack), `agents.md §5.4`/`§5.5`, `architecture.md §1.5`/`§1.7`.
**Implements ADR 0051** (the design); **amends ADR 0021** (pts 4, 6) and **refines ADR 0023** (pt 1's
retry-budget taxonomy) in code, as ADR 0051 recorded those would be. Preserves the ADR-0058 MITRE
pass-through verbatim.

## Context

ADR 0051 settled the design — the extraction-stage mechanical checks become one orchestrator-owned,
orchestrator-routed stack producing one findings set; the Extractor stops running its own hidden
`hallucination_retry` loop; the jury consumes the findings set instead of re-deriving the
search-before-claim trace check. This ADR records the **implementation decisions** that design left
open, per the CLAUDE.md "never resolve architectural ambiguities silently" rule.

## Decision

**1. Grounding findings route as RETRY, not as an ADR-0048 refinement patch.** ADR 0051's prose says
the orchestrator routes a grounding finding to the agent "which re-emits a **patch** (ADR 0048)", but
its own banner refines ADR 0023 *with the retry-vs-refinement split unchanged*, and the higher-authority
docs are explicit: `validation.md §6.10.1` mechanism 3 (grounding / search-before-claim) is **class
retry** — "same-or-re-prompted input, raising the agent-failure path on exhaustion" — and the hard rule
(`architecture.md §1.7`) is that retry and refinement never cross. Per the authority gradient (docs >
ADRs), the binding reading is **retry**: a grounding failure folds the findings into the prompt and
re-runs a *full* `extract` (mirroring the static-schema structural retry), never the jury-revise
`refine` patch. The word "patch" in ADR 0051 is the general stack description, not a routing mandate.

**2. The grounding-retry budget is orchestrator-owned (`DEFAULT_GROUNDING_RETRY_ATTEMPTS = 2`).** The
Extractor's former `hallucination_retry_attempts` (the budget ADR 0023 pt 1 said "the orchestrator
never saw") folds into a new orchestrator counter `grounding_attempts`, bounded by
`grounding_retry_attempts`, with its own no-progress early-bail (ADR 0057-style) and the global
iteration-cap backstop (ADR 0056). It is **independent** of the static-schema and refinement budgets
(the three never steal each other's budget). On exhaustion the run halts cleanly as
`HALTED_VALIDATION` — the relocation of the Extractor's former `ExtractionError`.

**3. One findings set, three sibling layers, two routing classes.** A new
`cyberlab_gen/validators/grounding_validator.py` (`GroundingValidator` → `GroundingResult`) is the single
home for: provenance-structure (relocated from the deleted `extractor_jury/verification.py`
`_check_structure`), search-before-claim trace (the *single* de-duplicated check, formerly in both the
Extractor and the jury), MITRE pass-through (no-op, ADR 0055/0058), and CVE-hallucination. The
search-before-claim and CVE findings are **retry-triggering**; provenance-structure findings are
**informational jury grounding** (carried to the jury, never auto-retried) — preserving the pre-batch
behaviour where the Extractor retried on trace/CVE problems while structure findings only informed the
jury's verdict.

**4. The jury consumes, it does not re-derive.** `ExtractorJury.review`'s `lookups` parameter is
replaced by `grounding_findings`; the jury no longer calls `verify_provenance`. The orchestrator
computes the findings once in the `grounding` node and passes them to `jury.review`. `verification.py`
is deleted; its generic provenance walker and structure check move into the grounding validator.

**5. Additive amendment to the ADR-0023-locked builder.** `build_pipeline` / `run_pipeline` gain
keyword-only `grounding_validator` (default-constructed when omitted, so every existing call site is
unchanged and behaviour-neutral for specs with no `external_api` claims) and `grounding_retry_attempts`
— the same additive discipline ADR 0040 (checkpointer) and ADR 0056 (global cap) used. The graph gains
a `grounding` node between `validate_static_schema` and `jury`: `extract → validate → grounding → jury
→ enrich`.

## Consequences

- **Architecture invariant restored** (`architecture.md §1.5`): no LLM-producing stage owns a
  framework-check retry budget; the orchestrator routes and owns the budget.
- **Drift eliminated**: the search-before-claim trace check exists once, not once in the Extractor and
  again in the jury.
- **C1 interlock (next item):** the `framework_enriched` exemption (ADR 0052) lands in this stack's
  single `_check_search_before_claim`, so framework-written `external_api` fields are exempt once
  enrichment precedes the grounding stack. C1 also moves `enrich` to before `grounding`/`jury`.
- **Recursion limit:** a grounding-retry loop is `extract → validate → grounding → extract` (3
  super-steps/iteration); the static-schema pathological loop is unchanged (grounding runs only after
  static passes), so `GRAPH_RECURSION_LIMIT = 4 × GLOBAL_ITERATION_CAP` still binds the semantic cap
  first. C1's `enrich` insertion revisits this.
- **`ExtractionResult.reprompts`** now reports only `refine`'s patch-apply retries (0 for a clean
  extract); the Extractor's content-retry budget is gone. `refine` keeps a small patch-*apply* re-prompt
  loop (`DEFAULT_PATCH_RETRY_ATTEMPTS`, a malformed-output recovery, `§6.10.1` mechanism 2); the
  whole-spec grounding re-check of a patch (R2) is preserved by the patched spec re-entering the
  orchestrator stack on the graph, not a hidden Extractor loop.
- **Docs:** no `docs/` edit — `validation.md §6.10.1`/`§6.10.2` and `agents.md §5.4`/`§5.5` already
  describe this end-state (they were updated in the ADR-0051 design pass).
