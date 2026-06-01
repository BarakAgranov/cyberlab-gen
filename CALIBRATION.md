# Calibration log

Per implementation-plan.md §1.7. Filled in as Phase 1+ locks empirical values.

All current values are **v1 placeholders** (`architecture.md §8.4`): no eval-harness
data exists yet to set them empirically. The eval harness (Task 8) produces the
false-approval / false-rejection rates that will drive the first real calibration.
Until then, the values below are the architecture's documented defaults.

## Asymmetric jury calibration (Extractor-Jury) — DISCIPLINE, not a tunable

Source: `agents.md §5.5`, `eval.md §7.5`.

For cyberlab-gen, **false-approval is costlier than false-rejection**: a bad
AttackSpec cascades through every downstream stage (Planner, Generators, Critic,
the generated lab itself), while a false-rejection only costs cycles. Therefore:

> **Tune the jury rubric floor *upward* on observed false-approval (tightening).
> Do NOT symmetrically tune it *downward* on observed false-rejection
> (loosening).**

The eval harness can drive the threshold in *both* directions algorithmically
(it measures both rates). This discipline overrides that symmetry: we
intentionally privilege stricter approval over jury throughput. A future change
that lowers the floor to reduce false-rejections is a calibration-discipline
violation and must be rejected in review, even if the eval numbers "support" it.

Encoded in code as: `ExtractorJury` exposes `rubric_floor` as a parameter (so the
harness can raise it) but the agent never lowers it autonomously, and there is no
code path that decreases the floor based on false-rejection observations.

## Phase 1 placeholder values

| Knob | Value | Where | Source |
|---|---|---|---|
| Extractor-Jury rubric floor (all 4 dimensions) | 0.7 | `extractor_jury.jury.DEFAULT_RUBRIC_FLOOR` | `agents.md §5.5`, `architecture.md §8.4` |
| Jury retry count (N) | 2 | (refinement coordinator, Task 6) | `agents.md §5.5`, `architecture.md §8.4` |
| Extractor completeness floor | 0.5 | `attack_spec.ExtractionMetadataBlock` | `agents.md §5.4` |
| Extractor structural-retry budget | 2 retries (3 attempts) | `call_surface.DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` | ADR 0018, `architecture.md §8.4` |
| Extractor hallucination/search-before-claim retry budget | 2 retries (3 attempts) | `extractor.extractor.DEFAULT_HALLUCINATION_RETRY_ATTEMPTS` | ADR 0021, `architecture.md §8.4` |
| Extractor agent-discretion external-call budget | ~10 / blog | (Task 6 wiring) | `schema.md §4.15` |
| Framework external-API enrichment budget | 100 / run | `framework.enrichment._DEFAULT_BUDGET` | `pipeline.md §3.2.4` |
| Auto-accept proposal cap (`--auto`) | 5 / run | (Task 7) | `schema.md §4.16` |

The two Extractor retry budgets are **independent** (ADR 0021):
structural-malformation (the model couldn't produce schema-valid JSON, owned by
the call surface) vs. hallucination/search-before-claim (the JSON is valid but
factually ungrounded, owned by the Extractor stage). Both are *retry*, never
refinement (`architecture.md §1.7`).
