# cyberlab-gen — Eval Harness

**Companion to:** `architecture.md` (hub).
**Document scope:** The eval harness — what it measures, how it measures it, what blogs it runs against, how it accommodates statistical variance, and (importantly) what it can and cannot honestly guarantee in an open-source single-user tool.

The eval harness is a first-class component of cyberlab-gen, not a deferred afterthought (`architecture.md §1.8`). Its primary role is comparing strategies so design decisions are made with evidence. Its secondary role is regression catching in CI. Its tertiary role is calibration of agent and jury thresholds.

---

## 7. Eval Harness

### 7.1 What this section covers

The eval harness is a first-class component of cyberlab-gen, not a deferred afterthought (per `architecture.md §1.8`). This section specifies what the harness measures, how it measures it, what blogs it runs against, how it accommodates statistical variance, and — most importantly — what it can and cannot honestly guarantee in an open-source single-user tool.

The harness's primary role is **comparing strategies** so design decisions are made with evidence rather than guesswork. Its secondary role is **regression catching** in CI. Its tertiary role is **calibration** of agent and jury thresholds.

### 7.2 The honest framing of OSS evaluation

Several conventional assumptions about ML evaluation don't hold for an OSS single-user tool. Stating them upfront prevents the architecture from claiming guarantees it can't deliver.

**Held-out integrity is best-effort, not absolute.** The blog set ships in the repo. Anyone — including maintainers tuning the system — can read every blog in the held-out set. The harness cannot prevent contamination. The only structural safeguard is **rotation per release**: each release rotates blogs between curated and held-out sets, so over time the held-out set is meaningfully different from what was visible during any specific tuning cycle.

**Three accepted pressure channels.** The architecture explicitly accepts that the following will happen and frames its evaluation honestly around them:

1. **Hand-tuning to the curated set.** Maintainers will read curated-set results, see failures, and adjust prompts/registries/strategies. This is normal development. The held-out set is the structural protection against curated-set overfitting.
2. **Telemetry-driven blog-set updates.** When users opt into telemetry submission (`pipeline.md §3.6`) and aggregated results show the system struggles on blog patterns absent from the curated set, the curated set will be updated to include representative blogs. This is healthy responsiveness to real-world distribution but it does mean the curated set evolves in a direction informed by failure modes.
3. **Rotation visibility.** The rotation policy itself (which blogs move between curated and held-out) is visible in the repo's commit history. A determined contamination would still be visible in the audit trail, but rotation alone is not cryptographic protection.

**No hidden eval set.** Some labs have private held-out sets. cyberlab-gen ships its blog list publicly. The honest cost: stronger overfitting risk. The honest benefit: contributors can independently verify eval results, propose new blogs, and reproduce comparisons.

**Mechanical metrics over subjective ones, where possible.** Subjective quality assessment by the Critic is part of the harness, but it's a softer signal than mechanical metrics (validator pass rates, cost per lab, structural completeness scores). The harness reports both with appropriate confidence framing.

This section's specifications operate within these constraints, not against them.

### 7.3 The blog set composition

The harness operates on two named sets, both checked into the repo at `eval/blog-sets/`:

**Curated set** — blogs visible to maintainers during development. Used for prompt tuning, strategy comparison, and active calibration. **Initial v1 size: 18 blogs** (v1 placeholder; refined by eval coverage analysis post-launch).

**Held-out set** — blogs reserved for measuring generalization. Maintainers should not read the held-out blogs while tuning; rotation per release moves blogs between sets to enforce structural distance over time. **Initial v1 size: 12 blogs.**

#### Coverage requirements

Coverage requirements apply to the union (curated + held-out), not each set separately. The required coverage:

- All three clouds + GitHub each represented (≥3 blogs per platform).
- Multi-cloud or multi-platform attacks present (≥2 blogs).
- Each thesis type from the v1 seed list (`schema.md §4.8`) represented (≥1 blog).
- **At least one blog targeting a non-first-class runtime** (per `schema.md §4.13`), to exercise the proposed-runtime + best-effort-coverage code paths.
- Each major lab-class-signal facet exercised (illustrative examples: `requires_infra`, `simulated_components`, `external_channel`, `multi_language`). These are illustrative; the actual coverage requirement is "representative diversity" rather than "every facet ever defined."
- Complexity tiers represented: simple (≤3 chain steps), medium (4–8), complex (9+).
- Both incident-analysis blogs (defender_techniques present) and pure-attack blogs.
- Both vulnerability-disclosure blogs (substantive `vulnerability_story`) and TTP-chain blogs.

**Coverage is overlapping by design.** With 30 blogs total against 8+ coverage dimensions, a single blog typically satisfies multiple requirements at once (e.g., a complex multi-cloud incident-analysis vulnerability-chain blog satisfies five requirements simultaneously). The harness emits a **coverage matrix per release** showing which requirements each blog satisfies — readers can verify completeness rather than assuming each requirement is met by a distinct blog.

#### Coverage-tag namespaces and the manifest index

Coverage tags live in two places: each walk's §14 (the full tag set for that blog) and the manifest's per-entry `coverage_tags` (the harness's **index** — a deliberate, disciplined **verbatim subset** of the walk's §14, not a reword; enforced by `tests/eval/test_manifest.py`). Three namespaces recur, with distinct jobs:

- **`target:*`** — the attack *surface* the Extractor is scored on. A blog-derived **facet** (`schema.md §4.13`); as a coverage tag it mirrors the walk's §6 facet.
- **`runtime:*`** — what the Planner *provisions* to reproduce the lab. A lab-derived **facet** (`schema.md §4.13`), carrying the `first_class` flag.
- **`platform:*`** — a set-level **coverage label** the harness counts for breadth (e.g. `platform:kubernetes`, `platform:github`). This is an **eval-only** namespace: it is **not** a facet and has no registry entry. It can legitimately coexist with a `target:*` facet for the same noun (e.g. `target:gke` the facet + `platform:kubernetes` the breadth label) because they answer different questions.

Assign by rule, not by guess: the surface under test → `target:`; what the lab stands up → `runtime:`; a breadth bucket the coverage matrix counts → `platform:`. `shape` is a descriptive label carried by the manifest `shape:` field (and the walk §1 header), **not** a coverage tag (ADR 0103).

**Initial v1 seed blogs** include the blogs walked during architecture design plus additional blogs identified during the curated-set buildout. The exact list lives in `eval/blog-sets/manifest.yaml`, versioned with the repo.

#### Rotation policy

At each release:

1. Maintainers identify blogs from the held-out set whose generalization signal has been "consumed" (results have been examined repeatedly enough that the held-out integrity is questionable).
2. Those blogs move into the curated set.
3. New blogs (or rotated-out blogs after a cooling period) move into the held-out set.
4. The rotation is recorded in the release notes.

The rotation cadence is roughly per-minor-release. Major releases may rotate more aggressively. The rotation manifest is part of the eval harness configuration; eval runs record which rotation generation they used.

### 7.4 Mechanical metrics

The harness records the following metrics per eval run, per blog. These are **objective and reproducible up to model non-determinism** (see §7.6). They form the spine of the harness's reporting.

**The harness reads these from the pipeline's own emitted run record — it does not recompute them.** The pipeline already runs the mechanical layers and emits its verdicts (e.g. the static-schema verdict, computed *with* the run's provisional-proposals context); the harness *measures pipeline output*. Re-running a validator outside the pipeline would risk a different result — e.g. a static-schema false failure from not re-applying the run's provisional proposals (`schema.md §4.16`) — so measurement reads pipeline truth, it never re-derives it. (This matches the `architecture.md §1.8` framing: the harness is a *peer that measures* the pipeline, not a second implementation of its checks.)

#### Validator pass rates (per pass)

Listed in the order the passes run (cheap→expensive). Report keys are descriptive (e.g. `static_schema`, `semantic_cross_check`), never `layer_N`.

- Static-schema validation: pass / fail.
- Semantic cross-check: pass / pass-with-warnings / fail.
- Containerized dry-run: pass / fail, with per-tool breakdown (ruff, mypy, terraform plan, tflint, tfsec, shellcheck).
- Real-platform apply: **always `skipped: v2-deferred` in v1** (per `validation.md §6.7`). The metric is preserved in the report structure so v2 adds the pass without renumbering.
- Safety scans: pass / fail, severity counts.

#### Cost per lab

- LLM tokens per agent (Extractor, Planner, per-phase Generator, Lab-level, Cleanup, Docs, Critic, Juries).
- LLM dollars per agent (per-model, per `pipeline.md §3.5` cost tracking).
- Total LLM dollars.
- Cloud platform dollars: always zero in v1 (real-platform apply is v2-deferred). Metric preserved in the report structure for v2 compatibility.
- Wall-clock time.

#### Structural completeness

- Manifest field coverage: percentage of optional content fields populated (vs. left as `unknown_from_blog`).
- AttackSpec field coverage: same.
- Per-step reproducibility distribution (how many `full` / `partial_simulation` / `demonstration_only` / `not_reproducible`).
- Lab-level reproducibility classification (derived from per-step values using the any-heterogeneity-mixed rule per `schema.md §4.8`).
- Number of registry proposals issued (value types and facets, separately).
- Number of `extras` entries at each level.

#### Refinement-loop metrics

- Iterations per agent.
- Total iterations.
- Best-state retention: which iteration's snapshot was shipped (1st, mid, last).
- Oscillation patterns detected (cycle / phase-level repeat / cascade).
- Cap-hit reasons (LLM budget / total iteration cap / per-agent cap).

#### Jury metrics

- Approval rate at each jury (Extractor-Jury, Planner-Jury).
- Average iterations to jury approval.
- Multi-model jury agreement rate (when used).
- **Rate of exhausted-retries shipping with `low_jury_confidence` flag** (per `agents.md §5.5`).

### 7.5 Subjective metrics

The Critic provides per-dimension scores (`agents.md §5.14`):

- Fidelity to blog.
- Completeness.
- Implementation correctness against attack semantics.
- Code quality.
- Doc quality.
- Cleanup quality.

Plus **per-phase confidence distribution** (per `agents.md §5.14`): how confidence varies across phases within a lab; whether confidence correlates with per-step reproducibility classifications.

These are **softer signals** than mechanical metrics but capture quality dimensions mechanical metrics can't. The harness reports them with calibration framing: Critic scores are tracked over time to detect drift; Critic scores from different model lineages are compared to triangulate.

**False-approval and false-rejection rates** are tracked separately from Critic scores. For each blog in the curated and held-out sets, maintainers periodically review jury decisions and Critic verdicts:

- **False-approval rate**: jury approved an AttackSpec or Manifest that the human reference assessment marks as needing revision. Costlier to the system because bad foundations cascade.
- **False-rejection rate**: jury demanded revisions on an AttackSpec or Manifest that the human reference assessment marks as acceptable. Costs cycles but doesn't corrupt outputs.

These rates calibrate jury thresholds (the floor in `agents.md §5.5` for the Extractor-Jury and `§5.8` for the Planner-Jury). For cyberlab-gen, false-approval is consistently treated as the costlier failure mode. **Asymmetric calibration is mandatory:** tune *upward* on observed false-approval (tightening), do not symmetrically tune downward on observed false-rejection (loosening). The eval harness can drive both directions algorithmically; the calibration discipline overrides by intentionally privileging stricter approval over jury throughput. Cross-reference `agents.md §5.5`.

**Paired rotation for held-out reviews.** Manual review of held-out jury decisions is itself a contamination event — each review burns a unit of held-out integrity. Without compensation, the held-out set would decay through normal maintenance faster than rotation can replace it. The discipline: **a held-out blog reviewed for calibration is automatically rotated to curated at the next release.** This makes the contamination event structural rather than incidental. Telemetry on review activity becomes a leading indicator of rotation pressure — when many held-out blogs have been reviewed, the rotation policy compensates by replacing them with fresh held-out blogs.

**Proxy metric for per-PR false-approval tracking.** False-approval rate per §7.5 is computed from manual review, which happens on a release-candidate cadence rather than per-PR. The proxy metric **jury-pass-but-Critic-fail** — cases where a jury approved an artifact but the downstream Critic flagged it as `refine` or `reject` — is computable per-PR and correlates with manual-review false-approval. PR-time CI gates the proxy; release-candidate gates the manually-reviewed truth (see §7.11).

### 7.6 Variance and reproducibility under model non-determinism

LLM outputs vary across runs even with the same input. The harness handles this explicitly.

**Repeated runs.** Each blog in the eval set is run N times per harness invocation (default N=3, configurable). Per-run metrics are reported individually; aggregate metrics are reported with mean, median, and inter-run variance.

**Variance reporting.** When variance is high (configurable threshold; default coefficient of variation > 0.3), the harness flags the blog as *high-variance* in the report. **The CV threshold applies to the primary comparison metric** (whatever the eval run's primary metric is — overall pass rate by default; cost when comparing strategies) **and to cost as a separate flag.** High-variance blogs are weaker signals for strategy comparison.

**Seed control where supported.** The harness fixes seeds when the LLM provider exposes a seed parameter, and records seed usage per run as metadata. **Important honest framing: provider-side seed determinism is not guaranteed even when supported.** OpenAI documents seed as best-effort; Anthropic does not currently expose a seed parameter. Reading "seed used" in the harness report does not mean "this run is reproducible." Variance reporting is the primary reproducibility signal; seed is metadata.

**Comparison statistical bar.** When comparing two strategies (e.g., fixed-N stopping vs. score-plateau stopping), the harness reports:

- Mean difference per metric.
- Bootstrapped 95% confidence interval on the difference (over the N×blogs samples).
- Per-blog win/lose/tie summary.

A strategy is declared "preferred" only when the confidence interval excludes zero on the agreed primary metric, across both curated and held-out sets. Otherwise, the harness reports "no significant difference" and design decisions defer.

### 7.7 Stopping strategy comparison

Per `architecture.md §1.7`, the refinement loop's stopping strategy is pluggable. v1 ships at least three:

1. **Fixed-N iterations.** Stop after a configured number of refinement iterations regardless of state. Baseline.
2. **Score plateau.** Stop when the combined validator+critic score's improvement is below threshold for K consecutive iterations. Adaptive.
3. **Validator+Critic verdict.** Stop *positively* when the Validator passes (no static-schema, semantic cross-check, containerized dry-run, or safety-scan findings above threshold) and the Critic's verdict is `approve`. Goal-oriented.

**Positive-stop vs. negative-end-conditions.** These strategies define *when to stop happily*. Negative end-conditions (refinement budget exhausted, abandonment) are handled by the coordinator regardless of strategy choice (`pipeline.md §3.2.12`): on budget exhaustion, the best snapshot ships with flags; true abandonment (no coherent artifact produced) does not ship.

The harness compares strategies by running the same blogs through the same pipeline with each strategy, reporting:

- Quality (mechanical pass rates + Critic scores) per strategy.
- Cost (LLM dollars, iterations) per strategy.
- Cost-per-quality ratio: how much each strategy spends per unit of quality achieved. **Quality is computed as a composite** of Validator pass rates (weighted by pass importance — static-schema and semantic cross-check weighted highest as foundational; containerized dry-run weighted middle; safety scans weighted highest for the security-boundary failures, low for medium-severity findings) and the Critic's overall score. The harness reports both the raw components and the composite; strategy comparison uses the composite as the denominator. The exact weights are a v1 placeholder pending eval-harness data; they're declared in the harness configuration alongside the strategy choices.

The eval harness's findings inform default-strategy selection, but the user can override via config. Different stopping strategies may suit different user contexts (CI quick-check vs. one-shot careful generation).

### 7.8 Strategy parameter tuning

Each strategy has parameters (fixed-N: the N value; score plateau: K and threshold; verdict-based: Critic score floor). These parameters need calibration.

**Calibration approach:**

1. Maintainers hand-set parameter ranges based on experience and theory.
2. Harness sweeps parameter values on the **curated set**.
3. Best parameters per strategy are evaluated on the **held-out set once per release cycle** — *not* as part of the sweep itself. The sweep stays on curated; the held-out evaluation is a single validation pass per release. This bounds held-out consumption to the same cadence as the paired-rotation policy from §7.5.
4. If held-out performance matches curated, defaults are updated.
5. **If held-out significantly underperforms curated, the parameter is overfit and the curated-set sweep is reconsidered.** "Significantly underperforms" is defined via the §7.6 statistical machinery: the held-out vs. curated difference is *outside* the bootstrapped 95% confidence interval on the primary metric. Differences inside the interval are within noise and don't trigger reconsideration.

This is the structural protection against parameter overfitting: held-out is the honest signal, consumed at a bounded rate. The repo's audit trail shows when defaults change and what evidence drove the change.

### 7.9 Telemetry → eval feedback loop

Per `pipeline.md §3.6`, users may opt into telemetry submission. Aggregated telemetry surfaces patterns the curated set may not represent:

- Blogs that consistently produce low-completeness AttackSpecs **relative to the configured completeness floor** (per `agents.md §5.4`). "Low" means below floor, not below an absolute number — what counts as low depends on the floor calibrated for that release.
- Phases that consistently fail the semantic cross-check (suggests manifest schema needs refinement).
- `unknown_from_blog.reason` values that recur across many users (suggests a missing registry entry or a Researcher-stage seam — see `pipeline.md §3.2.2` and `architecture.md §8.2`).
- Cost outliers (blogs that spend 5x average; suggests refinement-loop oscillation patterns the coordinator missed).

**Fix-session pattern feedback loop.** When users submit telemetry, the sanitized `fix_history.json` content (per `pipeline.md §3.6`) is part of what's collected. The harness aggregates fix-session patterns with **narrowed scope** — not all fix-session content can be safely aggregated across users:

- **Safely aggregatable across users:** patch diffs (largely context-free — a diff against a manifest field has the same meaning regardless of who applied it), validation findings on patches, fix-session outcomes (`resolved` / `suspended` / `abandoned`), recurring `unknown_from_blog.reason` strings (bounded vocabulary).
- **Not safely aggregatable across users:** conversational content (user-specific context, project names, environment quirks). The sanitization pass strips obvious identifiers, but conversational content carries enough context to risk re-identification when aggregated; the harness aggregates only the structured fields above, not free-form conversation.

Patterns that emerge from the aggregatable categories:

- Which kinds of runtime issues recur across users (e.g., "step 3 frequently fails with permission error X across multiple labs"). Suggests the per-phase Generator's prompt should anticipate that pattern.
- Successful fix patches that recur across users. Candidates for prompt overlay updates or registry `notes_for_generator` updates — but only via maintainer review, never auto-applied (the architectural decision in `architecture.md §8.1`).

This feedback **informs curated-set updates**, which is the honest framing: the curated set evolves toward representativeness of real-world usage, and the held-out set provides ongoing structural protection against overfitting to that representation.

**Sparse-telemetry early period.** In the early operational period — roughly the first six months after v1 ships — telemetry submissions will be sparse. The feedback loop architecture promises in this section won't fire meaningfully until some submission threshold is crossed. The architecture does not define this threshold (it depends on real-world adoption; we can't predict it). What this means in practice: the **curated set is the dominant quality signal in the early period**, and telemetry-driven evolution kicks in gradually as submissions accumulate. The harness reports submission counts per release so maintainers see when telemetry-driven signals become meaningful.

The feedback loop is **maintainer-mediated**, not automatic. Telemetry surfaces signals; maintainers decide which signals warrant blog-set updates, which warrant prompt/registry/strategy changes. The decisions are PRs with their evidence; nothing happens silently.

### 7.10 Empirical schema walk on the curated set

Before each release that modifies the schema, the harness performs an **empirical schema walk**: run the current pipeline on the curated set and inspect:

- How often each schema field is populated.
- How often each registry entry is referenced (bundled vs. overlay).
- What registry proposals were issued during the walk (and whether the proposed entries overlap existing ones the agent missed — a sign that registry browsing or naming guidance needs improvement).
- Where `extras` entries appear and what content they preserve.

**Walk timing relative to rotation.** When a release rotates blogs between curated and held-out (per §7.3), the schema walk runs against the **new curated set** (post-rotation) because the walk's purpose is informing the release that's about to ship, not the prior release. The release's eval report makes the rotation generation explicit so readers know which curated set the walk reflects.

This walk catches schema drift (fields that no longer get populated suggest deprecated coverage) and surfaces registry gaps (recurring proposal patterns across blogs suggest the bundled registry should incorporate those entries).

The walk is mechanical: the pipeline runs against curated blogs, the walk's report is generated from the resulting AttackSpecs and manifests. No new generation; just analysis of recent runs.

### 7.11 CI gates

The harness integrates with CI as follows:

**On every pull request:**

- Run the curated set. Two options for variance handling — the project picks one post-launch based on observed false-alarm rate:
  - **Option A (lower cost, noisier):** N=1 per blog. Compare against an N=3 baseline maintained on main-branch. Thresholds are widened to reflect N=1 vs. N=3 variance — pass-rate drop of 8 percentage points, cost increase of 30%, Critic dimension drop of 0.08. The wider thresholds match expected N=1 noise observed on the curated set.
  - **Option B (higher cost, tighter signal):** N=2 per blog. Tighter thresholds because N=2 has meaningfully less noise than N=1: pass-rate drop of 5 percentage points, cost increase of 20%, Critic dimension drop of 0.05. Doubles PR-time CI cost but reduces false alarms.

  Both options gate the **proxy metric for false-approval**: jury-pass-but-Critic-fail rate (per §7.5). Per-PR manual review of jury decisions isn't feasible; the proxy is computable per-PR and correlates with the manual-review truth. PR-time gates the proxy with threshold "increase > 2 percentage points relative to baseline."

- Block the PR if any threshold is exceeded.

**On release candidates:**

- Run the curated and held-out sets with N=3. Release-candidate runs cover **broader coverage less deeply** (N=3 across full sets) because the goal is catching regressions on the diversity dimensions §7.3 requires.
- Run the empirical schema walk (per §7.10) against the post-rotation curated set.
- Apply paired rotation discipline (per §7.5).
- **Manually review** jury decisions on held-out blogs to compute the real false-approval rate (per §7.5). Rotation compensates for the reviews consumed.
- Generate the release-eval report; reviewers approve based on it.

**Periodically (e.g., weekly main-branch in v1):**

- Run a deeper N=5 evaluation on a **subset** of curated-set blogs — the most representative blogs per coverage dimension. Periodic runs cover **narrower coverage more deeply** (N=5 on a subset, for stronger signal on the most representative blogs). This complements release-candidate runs: release candidates test breadth; periodic runs test depth.
- Track long-term trends.
- Real-platform apply is v2-deferred per `validation.md §6.7`; in v2, periodic CI will run the real-platform-apply pass against dedicated cyberlab-gen-eval accounts per platform. **v1 periodic runs are static-passes-only.**

The CI thresholds are configurable per repo policy. The defaults above are starting points; they tune based on the noise floor observed in the first months of operation.

### 7.12 What the harness does not do

A few things deliberately outside scope:

- **It does not assess the value of generated labs to actual learners.** Pedagogical effectiveness requires human user studies, which are out of scope for a single-user OSS tool. The harness measures fidelity, completeness, and mechanical correctness as proxies, with honest framing about the limitation.
- **It does not run continuous real-platform apply.** The real-platform-apply pass is v2-deferred per `validation.md §6.7` and `architecture.md §8.1`. In v1, the harness validates statically; the user runs labs against real platforms themselves.
- **It does not provide a leaderboard or ranking against other tools.** cyberlab-gen has no peer tools at v1; comparison frameworks across tools are speculative and out of scope.
- **It does not protect against deliberate adversarial contamination.** A maintainer could cherry-pick the held-out set, hand-tune to it, and ship. The architecture's defenses against this are: rotation visibility in commit history, public blog list (so contamination is auditable), and the social practice of contributor review. None of these are cryptographic guarantees.
- **It does not certify safety.** The Validator's safety scans catch accidents (against the canonical lab-credentials catalog per `validation.md §6.8`); the Critic catches drift; scope (`architecture.md §0.2`) is the primary defense. The harness measures these mechanisms' effectiveness over time but does not certify the lab is safe to run. The harness *does* track safety-scan finding rates over time to detect drift in the system's accident-prevention behavior — drift in finding rate indicates that the system is producing different-shape output than before, which may warrant investigation. The user takes responsibility when running the lab against their own systems; `cyberlab-gen fix` mode (`pipeline.md §3.4`) provides assistance with runtime issues.

### 7.13 The harness's own evolution

Like the curated set, the harness itself evolves. Specifically:

- **Metrics added.** As patterns emerge that current metrics don't capture, new metrics are added. Each addition is a release event with clear documentation.
- **Strategies added.** New stopping strategies, new jury configurations, new generation approaches are added as separate `Strategy` implementations and compared against existing baselines via the harness.
- **Calibration recalibrated.** When the underlying model landscape changes (new model releases, model deprecations), thresholds tuned against old models may need recalibration. The harness's role is making this recalibration evidence-driven rather than guess-driven.

**Recalibration releases.** A distinct release type from feature releases. Triggered by model lineage changes — a new major model from a configured provider (e.g., a Claude or GPT major version), or a configured model being deprecated. A recalibration release bundles **only re-runs of the calibration process** (parameter sweeps, jury threshold tuning, stopping-strategy comparison against the new model landscape) and ships updated default thresholds. It does not change architecture, schemas, prompts, or registries. Naming it as a release type makes its operational role explicit: when a major model lands, the threshold defaults that the architecture has been honest about needing calibration get re-derived against the new model. Without this, every threshold tuned against an old model is potentially miscalibrated when a new model is the active default — a real operational concern that becomes invisible if recalibration is implicit.

The harness is **part of the codebase**, not a separate thing. Its configuration lives in the repo. Its results live in eval reports archived in the repo (`eval/reports/`). Its test fixtures (the blog sets) live in the repo. This co-location ensures the harness can't drift away from the system it evaluates.

### 7.14 Section summary

The eval harness is the system's evidence engine. It compares strategies, catches regressions, calibrates thresholds, and surfaces patterns from real usage when telemetry is opted into.

Its honest framing acknowledges OSS realities: the held-out set is best-effort with rotation-per-release as structural protection; hand-tuning to the curated set will happen and is the curated set's purpose; telemetry-driven evolution of the curated set is welcomed and audited via PRs.

Mechanical metrics form the spine; subjective Critic scores complement them with appropriate calibration framing. Per-phase confidence (from the Critic, per `agents.md §5.14`) is tracked alongside whole-lab dimensions to validate that the system's "honest confidence" framing actually correlates with lab quality. False-approval and false-rejection rates calibrate juries with explicit asymmetric cost (false-approval is costlier).

The fix-session feedback loop (`§7.9`) gives the system a path from real-world user struggles back into prompt and registry improvements — maintainer-mediated, never automatic.

The harness does not certify safety, assess pedagogical value, or protect against deliberate contamination. It is honest about what it measures and what it doesn't.

---

*End of eval document. See `architecture.md` for the architectural framing of evaluation as a peer-not-afterthought, `pipeline.md §3.6` for telemetry mechanics, `agents.md §5.14` for the Critic's contract, and `validation.md` for the mechanical layers the harness measures.*
