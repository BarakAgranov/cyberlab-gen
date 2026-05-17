# 0005 — Confidence is exclusive to LLM_INFERENCE

**Date:** 2026-05-17
**Phase:** Phase 0 (post-Task 2)
**Architecture refs:** `docs/schema.md §4.9`, `docs/pipeline.md §3.2.4`, `docs/schema-details.md §3`

## Decision

`Provenance[T].confidence` and `Provenance[T].confidence_source` are valid only when `source == LLM_INFERENCE`. For every other source value (`BLOG_EXPLICIT`, `EXTERNAL_API`, `UNKNOWN_FROM_BLOG`, `USER_PROVIDED`), both fields must be `None`. The `_source_rules` validator enforces this.

## Context

`schema-details.md §3` left this open with a `# TODO(architecture): clarify whether external_api may carry a confidence (e.g., fuzzy-match score from a CVE lookup)`. The TODO speculated about probabilistic external sources, but examining the v1 pipeline reveals the question is broader than EXTERNAL_API alone.

`confidence` was introduced for `LLM_INFERENCE` — specifically, framework-computed or model-self-reported confidence about an LLM-inferred value. The architecture (`schema.md §4.9`) gives confidence a single role: signal to downstream consumers (Critic, juries, README presentation) how much to weight an LLM-derived value.

Other source values don't have that semantic in v1:

- **BLOG_EXPLICIT**: the blog said it. Citations are the verification mechanism, not a confidence score.
- **EXTERNAL_API**: pre-Planner enrichment (`pipeline.md §3.2.4`) is exact-match-only in v1. The framework runs ID-based lookups (CVE IDs, MITRE technique IDs, etc.) against authoritative sources. Either the lookup returns canonical data (no confidence needed, implicit 1.0) or it fails and the field becomes `UNKNOWN_FROM_BLOG`. The pipeline cannot produce a probabilistic EXTERNAL_API state.
- **UNKNOWN_FROM_BLOG**: there's no value to be confident about — the field is explicitly unknown.
- **USER_PROVIDED**: user input during an interrupt. No probabilistic interpretation applies.

The current `_source_rules` validator has a gap: it requires confidence for LLM_INFERENCE, forbids it for UNKNOWN_FROM_BLOG, but is silent on BLOG_EXPLICIT, EXTERNAL_API, and USER_PROVIDED. A poorly-constructed `Provenance[str](value="x", source=BLOG_EXPLICIT, citations=[...], confidence=0.5, confidence_source=FRAMEWORK_COMPUTED)` currently passes validation. That's drift waiting to happen.

## Alternatives considered

- **Source-by-source rules.** Rejected: five rules where one suffices; risk of inconsistency when a sixth source is added; doesn't make the underlying semantic explicit.
- **Leave the question open** (current state). Rejected: contracts that permit nonsense values invite the values, and code starts depending on the nonsense. The doc's `# TODO(architecture)` was overdue for resolution.
- **Confidence exclusive to LLM_INFERENCE** (chosen). One rule, captures the v1 semantic, generalizes.

## Consequences

- `Provenance[T]._source_rules` gets two new rules: `confidence is not None and source is not LLM_INFERENCE` raises; `confidence_source is not None and source is not LLM_INFERENCE` raises.
- The existing required-when rule (`LLM_INFERENCE requires confidence`) is preserved.
- The existing pairing rule (`confidence and confidence_source travel together`) is preserved.
- The existing UNKNOWN_FROM_BLOG-specific `confidence must be None` rule becomes redundant (the new general rule covers it). It is removed; the new rule's error message names the general principle.
- The UNKNOWN_FROM_BLOG citations check is preserved (it's a separate invariant).
- `schema-details.md §3`'s validator example gets the new rules added; the `# TODO(architecture)` comment is replaced with a pointer to this ADR.
- `cyberlab_gen/schemas/provenance.py`'s `_source_rules` validator gets the new rules.
- `tests/unit/schemas/test_provenance.py` adds tests for each non-LLM_INFERENCE source; the redundant UNKNOWN_FROM_BLOG-specific confidence test is removed.
- If Phase 2+ introduces description-search corroboration of LLM_INFERENCE values (see ADR 0006), a follow-up ADR proposes how `EXTERNAL_API` confidence is interpreted at that time.