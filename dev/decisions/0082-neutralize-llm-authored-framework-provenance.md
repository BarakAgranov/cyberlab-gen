# 0082 — Neutralize LLM-authored framework-only provenance at the extract seam

**Date:** 2026-06-16
**Phase:** 2 (audit follow-up; hardens Phase-1 extract pipeline)
**Architecture refs:** `architecture.md §1.5` (an LLM never modifies shared state outside its designated output; the framework owns its own provenance), `§1.6` (mechanical safety checks are never LLM-influenced), `schema.md §4.9` (framework-only authorship of enrichment-driven provenance). Relates to ADR 0052 / 0061 (the `framework_enriched` exemption) and ADR 0051 / 0060 (the orchestrator-owned grounding stack).

## Context

`Provenance.framework_enriched: bool` (`schemas/provenance.py`) marks a field the framework's pre-Planner enrichment pass wrote (`source: external_api` whose API-response citation *is* the evidence). It is load-bearing for a mechanical safety check: the grounding stack's **search-before-claim** layer EXEMPTS `framework_enriched` fields from the agent-trace requirement (`grounding_validator.py:187-192`), because the framework — not an agent — produced them. The top-level `material_discrepancies` list and the per-field discrepancy record (`discrepancy_with_blog` / `overridden_blog_value` / `discrepancy_classification`) are likewise framework-authored by enrichment (`schema.md §4.9`).

An audit (2026-06-16, the `framework_enriched` finding) found these fields are **not protected from LLM authorship**. The `Provenance` model only constrains `framework_enriched=True` to require `source=external_api` (`provenance.py:116-120`); it does not — and at the Pydantic layer cannot — constrain *who* set it. The Extractor's structured output uses the same `Provenance` model, so a hallucinating or prompt-injected Extractor can emit `source=external_api` + `framework_enriched=True` on a fabricated field. The pipeline order is `extract → validate → enrich → grounding → jury`, and enrichment treats an already-`framework_enriched` field as a **no-op** (`enrichment.py:505-508`). So an LLM-self-stamped field is **doubly** evaded: enrichment skips it, and grounding exempts it — the search-before-claim hallucination check is bypassed. That defeats a `§1.6` mechanical safety check via LLM output, violating `§1.5`.

## Decision

The framework **resets the framework-only provenance fields on every Extractor output, at the orchestrator's extract seam, before the spec reaches validation / enrichment / grounding.**

1. New leaf module `framework/provenance_guard.py` exposes `neutralize_framework_owned_provenance(spec: AttackSpec) -> AttackSpec`. It returns a copy with, on **every** `Provenance` in the spec: `framework_enriched=False`, `discrepancy_with_blog=False`, `overridden_blog_value=None`, `discrepancy_classification=None`; and the top-level `material_discrepancies` cleared to `[]`. Implemented as a recursive scrub over `model_dump()` keyed on the `Provenance` marker fields, then re-validated — so it tracks the schema without enumerating every nesting site.

2. **Reset, not reject.** Enrichment is the sole legitimate writer of these fields and runs *after* extraction, so at the post-extraction seam the correct value is always the framework default (False/None/empty). Resetting is robust and idempotent; rejecting would hard-fail an otherwise-valid extraction over a field the LLM had no business setting. The reset is silent — the field then faces the grounding check like any other agent-claimed `external_api` value (which is the point).

3. **Applied at `extract_node` (`orchestrator.py`), the single seam every Extractor output passes through** — first run, structural retry, and refinement patch all land at `state.spec = ...`. `state.extraction` keeps the **raw** agent output (audit trail of what the LLM actually emitted); `state.spec` is the framework-sanitized canonical artifact that flows downstream and is persisted.

4. **Scope includes the discrepancy record and `material_discrepancies`, not only `framework_enriched`.** They are the same invariant (enrichment-authored, `§4.9`); leaving them writable would be a half-fix (and resolves the audit's sibling low finding on `material_discrepancies`).

## Alternatives considered

- **Reject any LLM-supplied `framework_enriched=True`** (raise at the seam) — rejected: brittle; a non-malicious model quirk would abort a valid extraction. Reset is the conservative, recoverable choice.
- **Exclude the fields from the Extractor's structured-output schema** — rejected: `Provenance` is one shared model used across the whole spec; carving per-call LLM-facing schemas off it is fragile and would not cover the refinement-patch path. The seam reset covers every path in one place.
- **Make the grounding check distrust `framework_enriched` unless the field was actually enriched** — rejected: grounding has no clean signal for "was this enriched *this run*"; moving the guarantee upstream (the field is simply false until enrichment sets it) is simpler and stronger.
- **Do nothing (prose invariant only)** — rejected: `§1.6` mechanical safety must be enforced mechanically, not by trusting the LLM not to set a field.

## Consequences

- The grounding search-before-claim check can no longer be bypassed by LLM-authored `framework_enriched`; the legitimate enrichment exemption is unchanged (enrichment still sets the flag *after* this reset, and grounding still exempts those).
- `framework/provenance_guard.py` + `tests/unit/framework/test_provenance_guard.py` (poisoned-CVE reset, poisoned `material_discrepancies` clear, no-op on a clean spec). The integration behavior is covered by the existing extract-pipeline tests, which keep passing because legitimate enrichment runs after the reset.
- `state.extraction` and `state.spec` may now legitimately differ (raw vs sanitized); persistence and downstream already consume `state.spec`.
- Idempotent and cheap (one `model_dump`/`model_validate` per Extractor run).
