# ADR 0025: Phase-1 eval-harness shape (runner seam, metrics, manual-review tooling)

**Status:** Accepted (Phase 1 Task 8)
**Date:** 2026-06-01
**Decider:** Task 8 implementation agent

## Context

Task 8 builds the Phase-1 eval-harness additions (`eval.md §7.3`–§7.5,
`implementation-plan.md §4.2` "Eval harness Phase 1 additions" + §4.4). The
brief is precise about *what* to measure (Layer 1 pass rate, cost per
AttackSpec, structural completeness, registry proposals issued, `extras` count;
plus manual false-approval / false-rejection rates) but leaves several shapes
under-specified for Phase 1:

1. **`eval.md §7.3` is not promoted from ADR 0014.** The blog-set manifest shape
   lives in ADR 0014, not in `eval.md`. ADR 0014 is authoritative; the Phase-1
   loader reads exactly that shape. `eval.md §7.3` also names v1 set sizes
   (18 curated / 12 held-out) that are a *post-launch* target, not a Phase-1
   requirement — `implementation-plan.md §4.3` overrides for Phase 1: "grow to
   3–5 blogs." The implementation-plan wins for Phase 1 sizing.

2. **No live LLM provider exists in the build/CI environment.** Task 7's log
   already records that real provider-backed `extract` runs are deferred to
   Task 8 / the eval harness, and that Phase-1 *tests* use a fake `ExtractRunner`
   (no live provider, no cassettes). The harness must therefore be driven through
   an *injectable* runner so (a) `just verify`'s smoke test runs deterministically
   offline, and (b) a maintainer with a configured provider can run the real
   pipeline. This mirrors `eval.md §7.2`'s honest framing: the harness is
   first-class code whose *logic* (metric computation, aggregation, reporting,
   review tooling) is the deliverable, independent of whether a given invocation
   has a live model behind it.

3. **`RunResult` (ADR 0024) omits the Layer-1 result and per-run cost** the
   metrics need. The CLI's `RunResult` carries the enriched spec + proposals +
   discrepancies, but not the `Layer1Result` (pass/fail) nor a cost figure. The
   harness needs both. Rather than churn ADR 0024's locked CLI return type, the
   harness defines its own per-run record (`BlogRunRecord`) and a narrow
   `EvalPipelineRunner` protocol that yields one.

4. **"Structural completeness score" is named but not defined as a formula** for
   Phase 1. `eval.md §7.4` lists "Manifest/AttackSpec field coverage: percentage
   of optional content fields populated (vs. left as `unknown_from_blog`)" and
   "Number of `extras` entries." Phase 1 has only the AttackSpec; the formula
   needs pinning.

## Decision

**1. Injectable runner seam.** The harness depends on a narrow protocol
`EvalPipelineRunner.run_once(blog_id) -> BlogRunRecord`. The production runner
(`ProviderBackedEvalRunner`, a thin adapter over the Task-7
`PipelineExtractRunner` + the Task-6 `Layer1Validator`) requires a configured
provider; when none is configured `just eval` reports that cleanly and exits
without pretending to have results (honest framing, `eval.md §7.2`). Tests inject
a scripted fake. This is the same testability discipline as ADR 0024.

**2. `BlogRunRecord` — one pipeline run's measured outcome.** Fields:
`blog_id`, `run_index`, `shipped` (bool), `layer1_passed` (bool),
`cost_usd` (Decimal), `completeness_score` (float, the Extractor's own
`extraction_metadata.completeness_score`), `structural_completeness` (float, the
harness-computed coverage fraction — see (4)), `value_type_proposals` (int),
`facet_proposals` (int), `extras_count` (int), `verdict` (the jury `Verdict`),
`low_jury_confidence` (bool), and an optional `halt_reason`. An `InternalModel`
(it is a harness-internal measurement, not a shipped artifact), except the
archived report which is an `ArtifactModel` so it round-trips through YAML.

**3. Manual jury-decision review tooling.** A `JuryReviewLedger`
(`ArtifactModel`, archived) keyed by `blog_id`, each entry recording the
maintainer's mark per run (`correct` / `false_approval` / `false_rejection`).
The tool aggregates per-blog and overall false-approval / false-rejection rates
(`eval.md §7.5`). The mark is a *human* judgment supplied to the tool; the tool
does not itself decide correctness (that would make a mechanical/LLM judgment of
something §7.5 explicitly assigns to maintainer review). Asymmetric discipline
(`CALIBRATION.md`) governs how the *rates* feed threshold tuning — the tool only
*measures* both rates; it never auto-lowers a floor.

**4. Structural-completeness formula (Phase 1, AttackSpec only).** The fraction
of optional top-level content blocks that are populated:
`external_references`, `real_world_incidents`, `defender_techniques`,
`defenses`, `reproducibility`, plus `thesis` and `chain` (always present when
in-scope). Out-of-scope specs score 0 (they carry no content). This is a coarse
coverage proxy — `eval.md §7.4`'s "percentage of optional content fields
populated"; the per-field `unknown_from_blog` breakdown is a Phase-4 refinement
when the empirical schema walk (§7.10) lands. The Extractor's own
`completeness_score` is recorded *alongside* this harness metric, not in place of
it (the two answer different questions: the agent's self-assessment vs. an
external structural count).

**5. Report archive.** Each `just eval` run writes a timestamped YAML report to
`eval/reports/<rotation-gen>-<timestamp>.yaml` (an `EvalReport` `ArtifactModel`)
carrying the manifest's `rotation_generation`, per-blog aggregates (mean/median
Layer-1 pass rate, mean cost, completeness band), and the run records. This
satisfies the "reports archive cleanly to `eval/reports/`" exit criterion and
`eval.md §7.13`'s co-location requirement.

**6. Curated-set growth + long blog.** The manifest grows from the 3 Phase-0
placeholders to the 2 *real* walked blogs (`ai-assisted-aws-intrusion`,
`aws-codebuild-actor-id-regex-bypass`) plus a synthetic long-blog entry to
exercise chunking (`implementation-plan.md §4.6` risk). The placeholders whose
walks were never written are removed (they pointed at non-existent walk files);
the manifest's `walk:` paths now all resolve, and a smoke test enforces that.

## Alternatives considered

- **Drive the harness only through the real provider.** Rejected: no provider in
  CI; the smoke test could not run; the metric/aggregation logic would be
  untested. The seam costs one protocol and buys deterministic tests.
- **Reuse `RunResult` directly as the per-run record.** Rejected: it lacks
  Layer-1 pass/fail and cost, and adding those to the CLI's locked type churns
  ADR 0024 for a non-CLI consumer.
- **Compute false-approval mechanically (jury-vs-walk diff).** Rejected: §7.5
  assigns this to *maintainer* review; a mechanical diff would be a different
  (weaker) metric. The proxy metric (jury-pass-but-Critic-fail) is the mechanical
  one, and the Critic doesn't exist until Phase 3 — so Phase 1 has only the
  manual tool.

## Consequences

- `just eval` runs offline-clean (reports "no provider configured") and
  provider-backed when a key is present. The smoke test exercises the harness
  end-to-end with a fake runner.
- The harness's metric/aggregation/review logic is fully unit-tested; the only
  unexercised-in-CI path is the live provider call itself.
- CALIBRATION.md gains the Phase-1 locked values with their driving evidence;
  because no live eval data exists in this environment, the evidence is the
  curated-set *structural* signal (completeness band, Layer-1 pass rate on the
  fixture runs) plus the architecture defaults, explicitly flagged as
  awaiting first provider-backed run. This is honest per `eval.md §7.2`.

## References

- `eval.md §7.3`–§7.5, §7.13 — blog set, metrics, calibration, co-location.
- `implementation-plan.md §4.2` (eval additions), §4.3 (3–5 blogs), §4.4
  (calibration items), §4.5 (exit criteria).
- ADR 0014 — blog-set manifest shape (authoritative).
- ADR 0024 — `RunResult` / `ExtractRunner` seam (the pattern this reuses).
- `CALIBRATION.md` — asymmetric jury-calibration discipline.
