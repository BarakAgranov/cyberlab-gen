# 0006 — Inference corroboration via description-search (deferred)

**Date:** 2026-05-17
**Phase:** Deferred — revisit when Extractor and eval data exist (Phase 2+)
**Architecture refs:** `docs/pipeline.md §3.2.4`, `docs/schema-details.md §3`, `dev/decisions/0005-external-api-confidence.md`
**Status:** Open question. Recorded for future design; no v1 implementation.

## The idea

v1's pre-Planner enrichment is exact-match: the framework looks up entities the blog named explicitly (CVE IDs, MITRE technique IDs, etc.). LLM_INFERENCE fields — where the LLM filled a value the blog didn't directly state — are not verified against external sources.

This leaves a gap: an LLM can confidently infer the wrong canonical entity. For example, a blog describes "an outdated SSM agent leading to credential exposure" without naming a CVE; the LLM infers CVE-2024-1234 with confidence 0.6; CVE-2024-1234 turns out to exist but is unrelated to SSM agents. The user, downstream, sees a lab built around the wrong CVE.

Description-search APIs (OpenCVE, NVD keyword search, MITRE STIX content search) could close this gap. The framework would search using the blog passage or LLM reasoning text, get ranked candidates, and either corroborate the LLM's inference (top match agrees) or flag a disagreement (top match differs).

The match score from the description search is what would *legitimately* carry as confidence on the corroborated result — unlike v1's exact-match EXTERNAL_API (which is always implicitly 1.0).

## Why this is deferred to Phase 2+

1. The Extractor doesn't exist yet (Phase 1 work). Designing inference-corroboration against a hypothetical Extractor is premature.
2. No empirical data on LLM_INFERENCE quality. Once the Extractor is producing real outputs against the curated blog set, the eval harness can measure how often LLM_INFERENCE values are wrong and whether description-search would have caught them.
3. The architectural surface area is significant. Adopting this affects the enrichment trigger model, the discrepancy semantics, the EXTERNAL_API source's invariants, and downstream consumers (Critic weighting, README presentation). Worth doing once, well, with data.

## Open questions to resolve when the feature is on the table

1. **What does the framework search with?** The blog passage that led the LLM to its inference, the LLM's reasoning text, or a structured query the LLM constructs? Trade-offs around defensibility vs. focus.
2. **What's the match threshold?** Above which the API "agrees" and the inference is corroborated; below which the API "disagrees" or has nothing to say. Needs calibration data.
3. **What does a corroborated inference look like in Provenance?** Does the source become EXTERNAL_API with the match score as confidence (loses the original LLM confidence), or does it stay LLM_INFERENCE with an EXTERNAL_API citation (preserves audit, conflates state)?
4. **What happens on disagreement?** Override (source becomes EXTERNAL_API, value changes, discrepancy record preserves the LLM's pick), surface (post-Extractor interrupt flags as material discrepancy), or demote (LLM_INFERENCE stays, a `external_search_disagrees` flag is set for the Critic to weight).
5. **What happens when no candidate clears the threshold?** Keep LLM_INFERENCE as-is, downgrade to UNKNOWN_FROM_BLOG, or surface at the interrupt regardless?
6. **Budget impact.** Description search costs more per call than exact-ID lookup. The existing `external_api_budget` cap needs to account for this, possibly with a separate sub-budget for corroboration searches vs. enrichment lookups.

## Conditions for revisiting

- Phase 2+ when the Extractor is producing real LLM_INFERENCE outputs.
- Eval harness shows wrong-inference is a measurable quality problem.
- At least one external_data_sources registry entry supports description-search (some already do; see OpenCVE's `search` parameter, NVD's keyword search).

When these conditions are met, a new ADR proposes the specific design and supersedes this entry's "deferred" status.