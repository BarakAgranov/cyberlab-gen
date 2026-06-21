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

## Phase 1 locked calibration items (Task 8) — values + driving evidence

Source: `implementation-plan.md §4.4` (the six items Phase 1 locks), `eval.md
§7.4`/§7.5 (the metrics that drive them). The eval harness that produces the
driving evidence shipped in Task 8 (`eval/runner/`, invoked via `just eval`).

**Honest framing (`eval.md §7.2`).** No live LLM provider is configured in the
build/CI environment, so no *provider-backed* eval run has been performed yet
(`just eval` reports "no provider configured" and runs nothing rather than
fabricating numbers). The values below are therefore locked at the architecture
defaults, with the *driving evidence* being (a) the curated set's **structural**
signal — the harness's structural-completeness metric and Layer-1 pass rate over
the fixture runs — and (b) the documented architecture defaults
(`architecture.md §8.4`). Each row names what the **first provider-backed run**
will re-derive. This is the v0.2 calibration baseline; a recalibration release
(`eval.md §7.13`) updates it once real runs exist.

| Item (`implementation-plan.md §4.4`) | Locked value | Driving evidence / what the first provider-backed run re-derives |
|---|---|---|
| **Extractor token budget** (input + output) | Provider/agent default (no fixed cap; budget tracked per call via the Phase-0 cost ledger) | No observed token usage yet. The harness records `cost_usd` per `BlogRunRecord` and `mean_cost_usd` per blog (`eval.md §7.4` "cost per AttackSpec"); the long-blog fixture (`long-multi-stage-cloud-campaign`) is in the set specifically to surface the budget ceiling under chunking (`implementation-plan.md §4.6`). The first provider-backed run sets the cap from the observed per-blog token usage. |
| **Extractor per-stage retry count** | 3 attempts (2 retries) — `call_surface.DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` | Architecture default (`architecture.md §8.4`). The harness's Layer-1 pass rate (`overall_layer1_pass_rate`; exit criterion >=95%) is the structural-failure-rate signal that validates 3 is enough; re-derived against the observed structural-malformation rate. |
| **Completeness floor** | 0.5 — `attack_spec.ExtractionMetadataBlock` (`agents.md §5.4`) | Architecture default. The harness reports both the Extractor's self-`completeness_score` and an independent `structural_completeness` per run; the exit criterion is "completeness scores cluster in a defensible band." The first provider-backed run calibrates the floor against the curated set's natural completeness distribution. |
| **Extractor-Jury threshold (asymmetric)** | 0.7 — `extractor_jury.jury.DEFAULT_RUBRIC_FLOOR` (`agents.md §5.5`) | Architecture default. The manual jury-decision review tooling (`eval/runner/review.py`) produces per-blog **false-approval / false-rejection** rates (`eval.md §7.5`). **Asymmetric discipline (above) governs the direction**: the first provider-backed review tightens the floor *upward* on observed false-approval and never lowers it on false-rejection. |
| **Refinement caps (Extractor/Jury cycle)** | 3 iterations — `orchestrator.DEFAULT_REFINEMENT_CAP` (placeholder, revisited Phase 4) | Architecture default for the minimal Phase-1 coordinator (Task 6). The harness records the verdict + `low_jury_confidence` per run; the rate of cap-exhausted `revise` ships is the signal Phase 4 uses to revisit. |
| **Per-run auto-accept proposal cap (`--auto`)** | 5 — `extract.DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP` (`schema.md §4.16`) | Architecture default. The harness records `value_type_proposals` / `facet_proposals` issued per run (counted separately, `eval.md §7.4`); the first provider-backed run checks whether the curated set routinely exceeds 5 proposals/run (which would argue for raising the cap). |

These six rows are the Phase-1 calibration record for the `v0.2` tag
(`implementation-plan.md §1.7`: each tag's `CALIBRATION.md` is preserved with the
evidence that drove it). When the first provider-backed `just eval` run lands,
append a new section with the real numbers — do not rewrite this one (the
architecture-default baseline is part of the audit trail).

## Asymmetric jury calibration applies to the Planner-Jury too (Phase 2)

Source: `agents.md §5.8` (Planner-Jury), `eval.md §7.5`, ADR 0102.

The asymmetric discipline above is **per-jury and identical in direction** for the Planner-Jury:
tune its floor *upward* on observed Planner-Jury false-approval, never downward on false-rejection.
The Phase-2 review tooling (`eval/runner/review.py`) now tags each reviewed verdict with a
`JuryKind` (`extractor` | `planner`) and reports per-jury false-approval / false-rejection rates, so
the two juries are calibrated **independently** under the same rule.

## Phase 2 calibration items (Task 10) — UNLOCKED placeholders, pending the architect's plan-eval run

Source: `implementation-plan.md §5.4` (the six items Phase 2 locks), `eval.md §7.4`/§7.5. **The
plan-stage eval harness that produces the driving evidence shipped in Task 10** (`eval/runner/plan_*`,
invoked via `just eval --stage plan`). **No provider-backed plan run has been performed** (eval is
user-run, real money — `eval.md §7.2`); the values below are the architecture defaults and remain
**placeholders**. The architect's first provider-backed `just eval --stage plan` over the curated set
re-derives and locks them at the `v0.3` tag.

| Item (`implementation-plan.md §5.4`) | Placeholder value | Where | What the first plan-eval run re-derives |
|---|---|---|---|
| **Planner token budget** | Provider/agent default (no fixed cap; tracked per call via the cost ledger) | (Planner agent) | `PlanRunRecord.cost_usd` / `PlanBlogAggregate.mean_cost_usd` over the curated specs set the cap from observed per-blog token usage. |
| **Planner per-stage retry count** | Architecture default (`architecture.md §8.4`) | (plan refinement coordinator) | The semantic cross-check pass rate (`PlanEvalReport.overall_semantic_cross_check_pass_rate`; exit criterion ≥90%) validates the retry count against the observed structural-failure rate. |
| **Planner-Jury threshold (asymmetric)** | 0.7 — Planner-Jury rubric floor (`agents.md §5.8`) | `planner_jury` | The per-jury review tooling (`review.py`, `JuryKind.PLANNER`) produces Planner-Jury false-approval / false-rejection rates; tightened *upward* on false-approval per the discipline above (never loosened). |
| **`on_dependency_failure` default = `warn`** | `warn` — `PhaseBlock.on_dependency_failure` default | `schemas/manifest.py` | Confirmed `warn` post-evidence once real manifests show how often phases declare a non-default value. |
| **Per-run auto-accept proposal cap (`--auto`)** | 5 — `interrupt.DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP` | `cli/interrupt.py` | The Planner now contributes **facet** proposals too (`PlanRunRecord.facet_proposals` / `PlanBlogAggregate.total_facet_proposals`); the run checks whether the curated set routinely exceeds 5 facet proposals/run. |
| **Pre-Planner external-API budget per run** | 100 / run — `framework.enrichment._DEFAULT_BUDGET` | `framework/enrichment.py` | Calibrated against observed enrichment usage on the curated set. |

**Walk-review gate (ADR 0102) — CLEARED (human pass complete, 2026-06-20; ADR 0104).** The five
Phase-2 curated additions were agent-drafted, then reviewed against their source blogs in the human
ground-truth pass with corrections applied; the walks are now **verified/blessed** ground truth. This
clears **only** the human-pass gate. **The six values above remain locked/pending the separate paid
`just eval --stage plan` calibration run** — that gate has not happened. Lifting provisional does
*not* unlock, fill, or finalize any calibration value; those still depend on observed run data.

These rows are the Phase-2 calibration record for the upcoming `v0.3` tag. When the first
provider-backed `just eval --stage plan` run lands, append a new section with the real numbers — do
not rewrite this one (the placeholder baseline is part of the audit trail).
