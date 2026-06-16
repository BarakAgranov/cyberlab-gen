# 0085 — Scope the extract-seam reset to what the run authored

- **Status:** accepted
- **Date:** 2026-06-16
- **Deciders:** maintainer (architect), implementing agent
- **Narrows:** ADR 0082 (neutralize LLM-authored framework provenance)
- **Frame:** ADR 0086 (framework-owned-field guard principle) — this is the first guard
  re-homed onto "a path the field actually travels"

## Context

ADR 0082 added `neutralize_framework_owned_provenance(spec)` and called it at the
orchestrator's extract seam (`orchestrator.py:544`) on **every** Extractor output — first run,
structural retry, and refinement — resetting the framework-only provenance fields and clearing
the top-level `material_discrepancies` index. ADR 0082's threat model was the LLM **self-stamping**
`framework_enriched` (a `§1.6` mechanical-safety evasion); it did not model **legitimate
enrichment followed by a refinement**.

On a jury `revise`, `extractor.refine` returns `apply_field_patch(prior_spec, patch)` — the prior
(already enriched) spec with only the flagged paths overwritten. `result.attack_spec` is therefore
the **merged** spec, and `neutralize` ran on the whole of it. A field a prior iteration's
enrichment had legitimately marked as a blog-vs-NVD discrepancy (`source=external_api`,
`framework_enriched=True`, `discrepancy_with_blog=True`, `overridden_blog_value=<blog>`, plus a
`material_discrepancies` entry) arrives at the seam byte-identical (the patch touched a different
field). `neutralize` reset its markers and cleared `material_discrepancies`. The re-enrichment that
follows (ADR 0052/0061) then could **not** re-detect the discrepancy — the field is now
`source=external_api`, so `enrichment.py` reads no `blog_explicit` value (`blog_value=None`,
`discrepant=False`) and re-stamps `framework_enriched=True` **without** recording the discrepancy.
Net: a blog-vs-API disagreement the first jury saw was **silently dropped** from the shipped
artifact. The enrichment idempotency no-op (`enrichment.py:508`, whose comment says it exists to
prevent exactly this) was defeated because `neutralize` reset `framework_enriched=False` upstream.
The only test on the path asserted only `framework_enriched` survival, so the suite stayed green.
(Confirmed by a code trace + two independent adversarial repros on `main`; this is the architect
review's blocker #1, and it subsumes D5-02.)

Separately, two framework-owned fields had **no** guard at all (review #7 / reachability sweep):
`CveReference.source_of_record` (set by enrichment only on a *successful* lookup, so an
Extractor-authored value survives every skipped lookup as a forged authoritative-source claim) and
the lab-level `reproducibility` block (framework-DERIVED per `architecture.md §0.7`, but a plain
optional field on the Extractor's output schema with no derive code and no reset in Phase 1).
`real_world_incidents.status` was checked and is **not** a hole — it is legitimately
Extractor-authored from the blog until an incidents enrichment source lands (inventoried in ADR
0086 with that trigger).

## Decision

**Neutralize only the framework-owned fields the current Extractor output actually authored** —
the architect's invariant. Two seams, by what the run wrote:

1. **First run / structural retry / grounding retry** (the LLM (re)authored the whole spec):
   `neutralize_framework_owned_provenance(spec)` scrubs the **whole** spec — now extended to also
   null every `CveReference.source_of_record` and the lab-level `reproducibility` block (#7).
2. **Targeted-patch refinement** (the LLM authored only the patch `new_value`s):
   `neutralize_patch_provenance(new_value)` scrubs **only the patch sub-tree**, called inside
   `apply_field_patch` before the deep-set. The orchestrator no longer blanket-scrubs the merged
   spec on the refinement branch (`if is_refinement: state.spec = result.attack_spec`). The
   top-level `material_discrepancies` / `reproducibility` rollups are deliberately **not** touched
   on this path — a field patch addresses a content path, never a top-level index — so a prior
   iteration's legitimate enrichment survives.

`apply_field_patch` is the sole production refine-merge (`extractor.py:195`), so scrubbing there
covers the whole refinement path while keeping the agent layer out of framework sanitization
(`§1.5`). The compensating safety property is preserved: a patch still cannot author
`framework_enriched` / the discrepancy record / a `source_of_record`.

## Consequences

- **+** A blog-vs-API material discrepancy now survives a jury revise (regression test:
  `test_refinement_preserves_prior_enrichment_discrepancy`); the `§1.6` self-stamping guard is
  unchanged on first-run and now also enforced on patch content
  (`test_patch_cannot_forge_*`); `source_of_record` and lab-level `reproducibility` can no longer
  be LLM-forged (`test_neutralize_nulls_*`).
- **+** The reset scope now matches authorship, the principle ADR 0086 generalizes.
- **−** The refinement path's safety now depends on `apply_field_patch` being the sole merge seam.
  This is an architectural invariant (`architecture.md §1.7`), documented at both call sites.
- **Untouched:** the first-run threat model and behavior of ADR 0082 are unchanged; only the
  over-broad application to the *merged refine output* is withdrawn.
