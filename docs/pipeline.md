# cyberlab-gen — Pipeline

**Companion to:** `architecture.md` (hub).
**Document scope:** The generation pipeline (§3.1 through §3.2.13), cross-stage contracts (§3.3), the fix pipeline (§3.4, new in v1), provider abstraction (§3.5), telemetry (§3.6), and provider failure handling (§3.7).

This document specifies what each pipeline stage does, what it consumes and produces, what interrupts the user sees, how stages route on failure, and how the system handles real-world concerns like provider outages and budget caps. Component contracts (what each *agent* does within a stage) live in `agents.md`. Schema details (what each artifact contains) live in `schema.md`.

---

## 3. Pipeline

### 3.1 Shape

The pipeline is a deterministic state machine with typed cross-stage boundaries — every stage's output is a typed model that the next stage's input is validated against. No free-text passes between stages; if an LLM produces unstructured text, the stage's job is to convert it to structure before the next stage sees it. *(v1 implementation: LangGraph for orchestration, Pydantic AI for typed agents.)*

cyberlab-gen has **two pipelines**:

- The **generation pipeline** (`cyberlab-gen generate <url>`) is the headline path. It produces a lab from a blog URL. Specified in §3.2.
- The **fix pipeline** (`cyberlab-gen fix <lab-dir>`) is a separate, peer pipeline. It debugs an already-generated lab through an interactive REPL session. Specified in §3.4.

The generation pipeline runs in one of two modes:

- **`--interactive`** (default) pauses at typed-artifact interrupts so the user can review the AttackSpec and the LabManifest before they propagate downstream. Used for one-shot careful generation.
- **`--auto`** runs through without interrupts. Used for batch eval runs, CI, or experienced users who trust the system on familiar blog patterns.

**Headless usage.** When stdin is not a TTY (CI, scripts, pipes), `--interactive` is rejected at startup with a clear message pointing to `--auto`. The tool never hangs silently waiting for input that can't arrive.

### 3.1.1 Inline informational notices vs. typed-artifact interrupts

The pipeline emits two kinds of user-facing signals:

**Inline informational notices** surface at specific points in the pipeline as the framework detects conditions worth communicating. They do not gate the pipeline. Each notice has its own surfacing point; the framework attaches them to the run report and surfaces them in the CLI output.

Notices in v1:

- *Recent-CVE without public PoC* — surfaces during pre-Planner enrichment (§3.2.4) when an associated CVE has been published recently enough that public PoC content may be limited; informs the user that generation will proceed with reduced external grounding.
- *Out-of-scope content detected* — surfaces after the Extractor (§3.2.2) when the blog content cannot be turned into a lab the system can meaningfully produce.

**Notices are informational and do not gate the pipeline, with one exception:** the *out-of-scope* notice halts in `--auto` mode (because there's no useful work to continue with), and surfaces as a normal interrupt in `--interactive` mode where the user can choose to proceed anyway.

**Typed-artifact interrupts** pause the pipeline at structural boundaries. The user sees the typed artifact (AttackSpec after Extractor, LabManifest after Planner) and chooses among four options:

1. **Approve** — accept the artifact as-is; the pipeline continues.
2. **Provide natural-language feedback** — the upstream agent re-runs with the user's free-text feedback wrapped in a structured `UserFeedback` object.
3. **Edit in `$EDITOR`** — the user opens the artifact in their editor and edits directly. The tool re-validates the edited artifact structurally; if invalid, the editor reopens with errors as comments. User edits are subject to *structural* re-validation only; semantic correctness of user edits is the user's responsibility. Downstream stages (juries, Critic) may surface issues with edited content.
4. **Abort** — the run halts; no lab is produced; the run report records why.

**Cost estimate emission.** Refined cost estimates are emitted alongside the artifact at each interrupt so the user has updated information before each major spending stage. In `--auto` mode, cost estimates are written to the run report but the pipeline proceeds without waiting for confirmation, except for the budget-overrun case below.

**Budget-overrun interrupts (both modes).** The user-configured budget caps (LLM tokens via `--max-llm-cost` or config) are honored in both modes. When the framework estimates that the next stage or refinement iteration would push accumulated spend past a cap, it pauses and surfaces the choice to the user: raise the cap, abort, or proceed past the cap explicitly. This is the one exception to "`--auto` mode has no interrupts" — caps are the user's stated limit, and silently exceeding them is worse than briefly breaking the auto-mode contract.

In `--interactive` mode, typed-artifact interrupts and proposal reviews (see §3.2.5, §3.2.8) also surface budget warnings when relevant.

### 3.2 Generation pipeline stages

Stages, in order. Each stage is specified by what it inputs, what it outputs, what it does, and how its failures route. Agent-internal mechanics (prompt shape, tools, quality bar) live in `agents.md`.

#### 3.2.1 Ingestion

**Input.** A URL.

**Output.** Cached blog content plus structural metadata (URL, canonical URL, content hash, fetched-at timestamp, fetch method, word count, publisher domain).

**Responsibilities.** Fetch the URL with reasonable timeouts. Normalize encoding. Compute a content hash. Cache the result in `~/.cyberlab-gen/cache/<blog-hash>/`. Downstream stages read from the cache, never re-fetch.

This protects against the blog changing mid-pipeline. Protection against prompt-injection *within* the content is the Extractor's responsibility — see §3.2.2.

**Failure modes.** URL unreachable → fail with clear error. Content-quality judgment (is this a technical writeup, is this in scope) is *not* Ingestion's responsibility; the Extractor is the sole content-quality judge.

#### 3.2.2 Extractor

**Input.** Cached blog content + Ingestion metadata.

**Output.** A structured `AttackSpec` artifact (see `schema.md §4.8`).

**Responsibilities.** Read the blog, produce an AttackSpec mirroring the blog's narrative — chain steps, real-world incidents, defender techniques (for incident-analysis blogs), defenses, thesis, source provenance. For each chain step, capture the source blog passages associated with it (`chain_step_excerpts`); these flow downstream so per-phase generators can ground their output in real context without seeing the entire blog.

**MITRE and CVE validation.** A well-formed MITRE technique ID is accepted as-is — it is **not** checked against a local list (the bundled seed is not an authority, so an uncatalogued id is left unverified, never rejected — ADR 0055/0058). A CVE reference is verified against NVD when an adapter is wired and skipped gracefully when it is not; a CVE the wired NVD source has no record of is rejected and re-prompts the Extractor with the id flagged, counting against the stage's retry budget. (Ungrounded technique ids are caught by the jury's fidelity review until a MITRE adapter is wired.)

**Provenance discipline (categorical, per `schema.md §4.20`).** Every content field carries a source and citations reflecting what actually produced the value (`blog_explicit`, `llm_inference`, `external_api`, `unknown_from_blog`). Inference is allowed only when the schema field needs filling and the blog implies (rather than states) the answer; the inference is marked, cited, and never silently passed as `blog_explicit`.

**The `gaps` list.** A top-level enumeration of what couldn't be filled in from the blog. Separate from per-field `unknown_from_blog` provenance: `gaps` is for the user at the post-Extractor interrupt and for the Planner to know what's missing structurally; `unknown_from_blog` is per-field audit trail and signal for the Generator ("don't try to use this field").

**Researcher-stage seam.** When the Extractor cannot fill a field from the blog and external lookup might help, it sets `unknown_from_blog` with `reason: "requires external research"`. This is a documented `reason` convention that signals where a future Researcher stage would help, without requiring one to exist yet.

**Scope decisions vs. planning decisions.** The Extractor flags out-of-scope content (off-topic for cyberlab-gen entirely). The Planner decides whether the in-scope content yields a buildable lab. The Extractor sets a top-level `extraction_outcome` field (`in_scope` | `out_of_scope`) with a reason; this is what triggers the out-of-scope notice in §3.1.1.

**Chunking for long blogs.** When blog length exceeds the model's effective context window, the framework chunks; the chunking strategy and reconciliation logic live in implementation. Long blogs are a real v1 concern; the eval harness includes long-blog cases (see `eval.md §7.3`).

**Failure modes.**
- Blog unreadable or paywalled → caught by Ingestion; Extractor never sees it.
- Blog too short or too vague → Extractor produces a low-completeness AttackSpec with `extras.extraction_warning`; the Critic flags it later.
- Blog out-of-scope → Extractor sets `extraction_outcome: out_of_scope`; out-of-scope notice fires (halts in `--auto`).
- External API call fails → Extractor records the failure in the field's provenance, leaves the field as `unknown_from_blog`. Pipeline continues.

#### 3.2.3 Extractor-Jury

**Input.** The **enriched** AttackSpec (pre-Planner enrichment, §3.2.4, runs *before* the jury in execution order — see the §3.3 table) + the blog content + the Extractor's tool call trace.

**Output.** A structured verdict (`approve` / `revise` with feedback / `reject` with reason).

**Responsibilities.** Review the AttackSpec for fidelity to blog, completeness, and provenance correctness. The jury verifies every `source` claim: for `blog_explicit`, does the cited passage actually say what the field claims? For `external_api`, does the cited API response actually contain that value? For `llm_inference`, is the reasoning trace coherent? Because enrichment runs first, the jury reviews the **final** provenance — *what ships equals what was reviewed*. Framework-written fields are marked `framework_enriched: true` (`schema.md §4.9`): the jury treats those as trusted (the framework made the authoritative call) and does not require agent tool-call evidence for them — that requirement applies only to *agent-claimed* `external_api` fields.

**Framework acts on the jury's verdict.** The jury produces a judgment; the framework reads the verdict and decides what to do: `approve` → continue; `revise` → Extractor re-runs with feedback (counts against refinement budget); `reject` → pipeline halts with explanation.

**Disagreement-without-progress handling.** When juries disagree (multi-model split) or retries are exhausted: in `--interactive`, escalate to user with both opinions surfaced; in `--auto`, accept the lower-scoring assessment as conservative default and flag in the report as `low_jury_confidence`. When retries are exhausted, the framework distinguishes two cases:
- (a) jury verdict is `reject` with a fundamental concern → halt;
- (b) jury verdict is `revise` with the same feedback unresolved → proceed with the last AttackSpec carrying a `low_jury_confidence` flag and the unresolved feedback in the run report.

The 0.7 jury approval floor and N=2 retry count are tunable defaults pending eval-harness data (`architecture.md §8.4`).

#### 3.2.4 Pre-Planner enrichment (framework, not agent)

**Execution order: enrichment runs *before* the Extractor-Jury (§3.2.3), on the Extractor's raw output** — so the jury reviews the enriched spec and *what ships equals what was reviewed*. (The section number predates this ordering; the cross-stage table in §3.3 shows the real sequence. Renumbering is deferred to avoid breaking the many `§3.2.x` cross-references.) On a jury `revise`, the Extractor patches the flagged fields and enrichment re-runs on the patched spec before the jury re-reviews, so the invariant holds across refinement iterations.

**Input.** The Extractor's AttackSpec (pre-jury).

**Output.** An enriched AttackSpec with additional provenance records from authoritative external sources.

**Responsibilities.** Deterministic framework pass that runs `enrichment_triggers` from the external_data_sources registry. Mandatory; never delegated to an agent.

For example: every CVE ID in the AttackSpec gets enriched with NVD data, KEV inclusion, EPSS score, and MSRC data (for Microsoft CVEs) before the AttackSpec is finalized. The framework — not an agent — sets the field's `source: external_api` with citations to both the blog passage and the API response.

This is the "framework-only-authorship" rule from `schema.md §4.9`. **Every field enrichment writes or rewrites is stamped `framework_enriched: true`** (`schema.md §4.9`), distinguishing the framework's own authoritative API call (trusted — no agent tool-call evidence required) from an *agent-claimed* `external_api` field (which must be tool-backed). When the API contradicts a `blog_explicit` Extractor finding, the framework rewrites the field with `source: external_api`, both citations preserved, and `discrepancy_with_blog: true`; *every* such rewrite is recorded so it remains auditable.

**Materiality-scaled surfacing.** Not all discrepancies warrant interrupting the user. The system distinguishes:

- *Non-material discrepancies* — same-tier CVSS, same CWE category, same affected product list, equivalent technique mapping. The rewrite happens silently (the discrepancy is still recorded in the provenance for audit); the Generator and docs use the API value going forward. The Critic notes these as part of its quality assessment without blocking.
- *Material discrepancies* — cross-tier CVSS difference (e.g., medium vs. critical), different attack vector, different CWE category, different affected product list, contradicting MITRE technique. These surface at the post-Extractor interrupt (§3.2.5) as a third review surface so the user sees the disagreement before downstream work is built on a silently-resolved choice. The user can accept the API value, accept the blog value (via natural-language feedback to the Extractor), or abort.

The exact materiality criteria per source live in implementation (in the `external_data_sources` registry entry's `discrepancy_materiality_rules` field). The architectural commitment: blog-vs-API disagreements are always recorded; material ones always surface; the framework never silently resolves a disagreement that would change the lab's character.

In `--auto` mode, material discrepancies are noted in the run report rather than surfacing as an interrupt — `--auto` accepts the API value with the discrepancy flagged. Users who don't want this behavior should use `--interactive`.

**External API budget.** A per-run cap on framework-issued external calls (default 100, configurable). When the AttackSpec implies more lookups than the cap allows, the framework runs the highest-priority cap's worth and skips the rest with `unknown_from_blog` reasons indicating which calls were skipped. Priority order: CVEs > MITRE techniques > GitHub repos > security bulletins > other authoritative sources.

**Rate-limiting.** When a mandatory enrichment source is rate-limited mid-run, the framework records the skipped lookups with `unknown_from_blog.reason: "external API rate-limited at enrichment time"`. The lab still generates; the missing data shows up in provenance and may surface in Critic concerns.

**User visibility.** Enrichment-overridden fields are visible to the user at the post-Extractor interrupt, with both citations present so the user can see any blog-vs-API discrepancy.

#### 3.2.5 Post-Extractor interrupt (interactive mode only)

In `--interactive`, the pipeline pauses here. The user sees **three distinct review surfaces** at this interrupt:

**For the AttackSpec itself**, the four-option menu from §3.1.1: Approve / Natural-language feedback (Extractor re-runs) / Edit in `$EDITOR` / Abort.

**For each registry proposal** (see `schema.md §4.16`): Accept or Edit. Edited proposals go through the same revalidation-with-comments loop as artifact edits — structurally invalid edits reopen the editor with errors as comments.

**For each material blog-vs-API discrepancy** flagged during enrichment (§3.2.4): the user reviews both citations and chooses: Accept the API value (default; framework already rewrote), Accept the blog value (provides Extractor feedback to revert), or Abort. Non-material discrepancies are listed in the report but don't require a per-discrepancy action.

> The per-proposal menu offers Accept and Edit. Rejecting a single proposal in isolation has no coherent semantics — the value exists in the AttackSpec, and the system requires typed values. A user who disagrees has three real paths: Edit the proposal to what they think is right; provide Extractor feedback at the AttackSpec level (option 2 of the four-option menu above) so the proposal disappears or changes when the Extractor re-runs against the corrected understanding; or Abort if no good path forward exists.

Refined cost estimate is also surfaced here so the user has updated information before approving and continuing into the Planner stage. If the estimated Planner cost would push accumulated spend past the configured cap, the budget-overrun interrupt (§3.1.1) takes precedence — user must raise cap, abort, or explicitly proceed past the cap.

In `--auto` mode, this stage does not exist; the pipeline continues directly from enrichment to Planner. Proposals are automatically accepted into the overlay and surfaced in the run report. The one exception: budget-overrun interrupts apply in both modes.

#### 3.2.6 Planner

**Input.** The enriched (and possibly user-edited) AttackSpec + the user's optional preferences (e.g., `preferred_clouds`, if set in config).

**Output.** A draft `LabManifest` artifact (see `schema.md §4.4`) with phases, lab resources, prereqs split into pre_lab/mid_lab, inputs, outputs, facets declared, per-step reproducibility carried forward, re-keyed per-phase excerpt bundles. No code paths yet.

**Responsibilities.** Consume the AttackSpec, decide which chain steps become phases, which become steps within phases, which become lab resources, which become manual prerequisites. Carry each step's reproducibility classification forward from the AttackSpec unchanged — the **Extractor** assigned it by applying the preference ordering from `schema.md §4.20`; the Planner does **not** re-apply the ladder or re-evaluate the tier (`architecture.md §0.7`). Phase shape emerges from the mix of carried-forward step classifications. Declare which facets the lab uses — `runtime:*` (lab-derived; Planner is the sole authority) and lab-derived `lab_class_signal:*` (`simulated_components`, `multi_language`, `parameterized`, etc.). `target:*` and blog-derived `lab_class_signal:*` are inherited from the AttackSpec (where the Extractor authored them).

**The Planner does not propose value types.** If the Planner needs a value type not in the AttackSpec, that's a signal the Extractor missed something; the Planner-Jury flags this and the refinement loop routes back to the Extractor.

**Provenance discipline.** The Planner inherits the AttackSpec's provenance and may add its own. Decisions like "these three blog steps become one phase" are Planner inferences recorded with `source: llm_inference`, with the AttackSpec chain steps cited and the Planner's reasoning as the inference trace.

**Credentials are not a planning concern.** Credentials, regional configuration, and per-platform tooling are *not* checked at planning time — they're handled by the generated lab's `prereqs.pre_lab` checks at run time. The Planner doesn't look at the user's cloud credentials; the generated lab will.

**Failure modes.**
- AttackSpec gaps too large to plan around → Planner refuses with `cannot_plan` error in both modes. The error report includes structured detail identifying *which* AttackSpec gaps prevented planning (specific fields, specific missing chain-step content). In `--interactive`, the user fixes them by re-running with Extractor feedback at the post-Extractor interrupt. In `--auto`, the run halts with the gap report written to the working directory; the user re-runs in `--interactive` (or fixes gaps via config) and re-runs `generate`.
- AttackSpec implies infrastructure the system cannot express as code → Planner refuses with `cannot_plan` error and structured reason (rare with the open-runtime model; typically the reproducibility ladder drops individual steps instead).
- AttackSpec is incoherent in a way the Extractor missed (mismatched preconditions/postconditions) → Planner flags and the refinement loop routes back to the Extractor. The Planner does not repair AttackSpec content; the fact that the Planner can see the incoherence doesn't grant it authority to fix it (see `agents.md §5.20` ownership rules).

#### 3.2.7 Planner-Jury

**Input.** The draft LabManifest + the AttackSpec + the Planner's reasoning trace.

**Output.** A verdict, same shape as Extractor-Jury.

**Responsibilities.** Verify Planner decisions trace to AttackSpec content. Phase decomposition is reasonable. Facets declared *by the Planner* (`runtime:*` and lab-derived `lab_class_signal:*`) match what the manifest fields imply. Facets inherited from the AttackSpec were already reviewed by the Extractor-Jury and are taken as-is. LabManifest-level fallback decisions per `schema.md §4.20` are documented honestly (no shortcut to demonstration-only when full was achievable). Generator-level fallbacks are reviewed by the Critic, not the Planner-Jury.

The Planner-Jury also reviews any `runtime:*` or lab-derived `lab_class_signal:*` facet proposals the Planner emitted, with the same Accept/Edit semantics as the Extractor-Jury reviews value-type proposals.

Same disagreement-without-progress handling as Extractor-Jury (§3.2.3): exhausted retries with `revise` verdicts → proceed with `low_jury_confidence` flag; exhausted retries with `reject` verdict → halt.

#### 3.2.8 Post-Planner interrupt (interactive mode only)

In `--interactive`, the pipeline pauses here. The user sees two distinct review surfaces:

**For the LabManifest itself**, the four-option menu from §3.1.1: Approve / Natural-language feedback (Planner re-runs) / Edit in `$EDITOR` / Abort.

**For each facet proposal from the Planner** (see `schema.md §4.16`): Accept or Edit. Edited proposals are revalidated; structurally invalid edits reopen the editor with errors as comments. (The Planner does not propose value types; value-type proposals all come from the Extractor and were reviewed at the post-Extractor interrupt.)

Refined cost estimate is surfaced here. If the estimated Generator cost would exceed the budget cap, the budget-overrun interrupt (§3.1.1) takes precedence.

In `--auto` mode, this stage does not exist; the pipeline continues directly into the Generator. The budget-overrun exception applies in both modes.

#### 3.2.9 Generators (four agent types; per-phase parallel, others serial)

The Generator stage is four agent types running in a specific order:

1. **Per-phase Generators** (one instance per phase, parallelized when phases are independent) — generate the implementation of each phase: Python module with `run_phase(config) -> dict`, optional phase-specific IaC, per-phase `cleanup.sh`, any payload files.

   **Phase independence definition.** Two phases are independent iff they share no declared inputs (no `bind_inputs` from one to the other) AND no overlapping `produces_world_state` items (neither phase mutates state the other reads or writes). Both conditions are necessary: `bind_inputs` catches formal data flow; `produces_world_state` overlap catches shared cloud state that two phases might mutate concurrently. The framework computes the phase DAG from the manifest and parallelizes independent phases; the per-phase Generator does not decide its own parallelism.

2. **Lab-level Generator** (serial, after per-phase Generators complete) — generates lab-level orchestration: `setup.sh`, lab-level IaC (Terraform by default, alternative when declared), entry-point script.

3. **Cleanup Generator** (serial, after Lab-level Generator) — generates the lab-level `cleanup.sh` orchestrator (which calls per-phase cleanup scripts in reverse-DAG order, handles cross-phase shared state, runs `terraform destroy` for lab-level resources) plus the `verify.sh` script. Lab-level cleanup orchestration is owned by this agent; per-phase cleanups are written by the phase agents.

4. **Docs Generator** (serial, last) — generates `README.md` at lab root plus the `docs/` directory: `attack_guide.md`, `concepts.md`, `attack_narrative.md`, `real_world_examples.md`, `prerequisites.md`, `defender_techniques.md` (when applicable), plus `detection/mitre_mapping.md` and `detection/cnapp_mapping.md`.

**Per-phase Generator behavior on missing value types.** Per-phase Generators have read-only access to value types; they cannot propose new ones. If a Per-phase Generator finds itself needing a value type not in the manifest, it fails the stage with `missing_value_type` error; the refinement coordinator routes back to the Planner (or further upstream to the Extractor if the type wasn't in the AttackSpec).

**Cross-phase coupling rules.** No phase's Terraform may reference another phase's Terraform directly. Cross-phase data flow goes through declared phase outputs in the manifest. Per-phase IaC references lab-level state via `data "terraform_remote_state"` (or equivalent for non-Terraform mechanisms). The Validator's semantic cross-check verifies this contract (`validation.md §6.5`).

**Failure modes.**
- Per-phase Generator emits code that doesn't implement a declared step → the Validator's semantic cross-check catches.
- Per-phase Generator hallucinates a cloud API → the Validator's containerized dry-run catches via the per-cloud catalog.
- IaC references a missing lab-level output → the Validator's semantic cross-check catches.
- Generator produces 200 lines for what should be 30 → Critic flags as over-engineering.

#### 3.2.10 Validator (mechanical layers)

Detail in `validation.md`. Brief here:

**Input.** Working directory containing the generated lab.
**Output.** A structured `ValidationReport` per layer + overall verdict.

The static-schema, semantic cross-check, containerized dry-run, and safety-scan passes always run in v1; the real-platform apply pass is v2-deferred per `architecture.md §8.1` — its slot stays reserved (between the containerized dry-run and the safety scans) so v2 adds it without renumbering. The passes run cheap-to-expensive; cheap passes run on every refinement iteration, expensive ones once before final output.

The Critic is a peer stage to the Validator (not a layer within it) — see §3.2.11.

#### 3.2.11 Critic

Detail in `agents.md §5.14`. Brief here:

**Input.** The generated lab + the AttackSpec + the ValidationReport + relevant blog excerpts.
**Output.** A `QualityReport` with per-dimension rubric scores, per-phase confidence scores, structured concerns list, and a verdict (`approve` / `refine` / `reject`).

Per-phase confidence feeds the README's "How to use this lab" section and the `validation-report.md`. The Critic is advisory: a `reject` verdict after exhausted refinement does not block shipping. The lab still ships, with the rejection prominently surfaced in the validation report. See `agents.md §5.14` for the full semantics and `architecture.md §1.6` for the locked invariant.

#### 3.2.12 Refinement loop

**Input.** ValidationReport + QualityReport.
**Output.** Updated lab directory; loop continues until pass / abandon / cap hit.

**Responsibilities.**
- For each validation failure or quality concern, identify the responsible agent and re-run it with the prior artifact plus the structured findings; the agent returns a **patch** of only the flagged field paths, which the coordinator deep-sets onto the prior artifact and re-validates (`architecture.md §1.7`, `schema.md §4.9`).
- Re-validate.
- Track iteration count and accumulated LLM cost.
- Stop when validator passes and quality is acceptable, abandonment criteria met, iteration cap hit, or cost cap hit.

The framework reads each failure (validator finding or quality concern) and re-runs the responsible agent. The agent doesn't choose to be re-run; the framework decides — preserving the `architecture.md §1.5` invariant.

**Stopping strategies are pluggable.** v1 ships three: fixed-N iterations (baseline), score plateau, validator+Critic verdict. The eval harness compares them; users can select via config. See `eval.md §7.7`.

**Cost discipline.** Per `architecture.md §1.7`, refinement is bounded by the everyday budget ($10 default LLM spend, configurable via `--max-llm-cost`) OR 20 total iterations OR 5 per agent, whichever hits first; the predictive budget-overrun interrupt (§3.1.1) fires before an iteration whose estimated cost would cross the everyday budget, in both modes. The separate **$25 catastrophe ceiling** is a hard backstop enforced on every billed call — success or failure (ADR 0047) — independent of this loop; it stops a runaway the predictive interrupt misses.

**Why both per-agent and total caps.** The per-agent cap (5) sums across agents to more than the total cap (20). The per-agent cap is a *fairness* mechanism — it prevents one agent from consuming the entire iteration budget while others get nothing — not a budget mechanism. The total cap is what binds in typical practice. Per-agent caps matter for pathological cases where one agent fails repeatedly while others would have produced clean output if given iteration room.

**Oscillation handling.** Three patterns the coordinator distinguishes:

- **Cycle.** Stage A and Stage B fight each other across iterations (A's regen breaks something B fixed; B's regen breaks something A fixed). Resolution: run A, then run B against A's output as a single coupled re-generation, then accept the result without further iteration on this pair. The cycle is broken by removing the iteration entry point for these two stages. **Cycle-resolved pairs are locked for the remainder of the refinement loop.** The iteration-causality log records cycle-resolved pairs; the coordinator refuses to route to either A or B in the pair when a cascade trigger comes from a third agent affecting both. The lock releases only at refinement-loop termination (success, budget exhaustion, or abandonment). Without this, cycle-break would be fragile to re-entry from a different cascade trigger.
- **Phase-level repeat.** The same phase fails the same way across iterations. Detected per-phase via the 5-iterations-per-stage cap. Resolution: ship the best snapshot of that phase with an `iteration_cap_exhausted_for: phase-N` flag in the report.
- **Cascade.** An upstream change ripples through downstream stages, exhausting downstream caps while the upstream is unchallenged. Resolution: when a downstream stage exhausts its cap, the coordinator inspects whether the failures all trace to a single upstream change (using the iteration-causality log it maintains for cap enforcement); if so, route the next iteration to the upstream agent with cumulative downstream failures as context.

**Iteration-causality log.** The coordinator maintains a log mapping each iteration to (responsible agent, triggering failure, resulting changes). This log enables cascade detection and is included in the run report for user diagnosis.

**Outcome distinctions.**

- **Budget exhausted, last verdict was `revise` or `refine`** → ship best snapshot with flags. The lab is the user's; they can try it and use `fix` mode for runtime issues.
- **Budget exhausted, last verdict was Critic `reject`** → still ships best snapshot; the rejection is prominently surfaced in `validation-report.md` and per-phase confidence flags in the README.
- **Cascade-abandoned (no coherent artifact produced)** → does not ship. The user receives a structured failure report with iteration history. Per `architecture.md §0.5 criterion 2`, true abandonment is rare and reserved for cases where no usable lab was produced at all.

**Best-state retention (secondary safety net).** Targeted patch refinement (`architecture.md §1.7`) makes per-field regression impossible *within* an artifact — only flagged fields are written — so snapshots are no longer the primary convergence mechanism. They remain a fallback that bounds *cross-phase* oscillation (a coupled re-generation of one phase can still perturb another; see the Cycle/Cascade patterns above) and preserves the best result on budget exhaustion. Each iteration's lab state is snapshotted to working directory subdirectories (e.g., `iter-3/`, `iter-4/`). The coordinator retains the top-3 by combined validator+quality score plus the most recent. On budget exhaustion, the highest-scored snapshot is shipped, with its iteration number and score history surfaced in the run report. (Snapshots beyond the top-3 are pruned during the run; on abandon, all retained snapshots and the iteration-causality log are preserved for user diagnosis.)

#### 3.2.13 Output

**Input.** Validated lab + final reports.

**Output.** A lab directory written to the user's chosen path:

```
lab/
  README.md                       ← root-level overview, includes "How to use this lab"
                                    section with per-phase confidence summary
                                    and `cyberlab-gen fix` pointer
  validation-report.md            ← human-readable validation + Critic report
  lab.yaml                        ← manifest
  infra/                          ← lab-level IaC (Terraform by default)
    main.tf
    outputs.tf
    variables.tf
  attack/
    phase_1_initial_access/
      infra/                      ← phase-specific IaC (if needed)
        main.tf
      attack.sh
      cleanup.sh                  ← per-phase cleanup, written by phase Generator
    phase_2_credential_harvest/
      attack.py
      cleanup.sh
  detection/
    phase_1_initial_access/
      sigma.yml
      sentinel_kql.yml            ← when blog uses Sentinel/KQL
      splunk_spl.yml              ← when blog uses Splunk/SPL
    mitre_mapping.md
    cnapp_mapping.md
  setup.sh                        ← orchestrator (supports --from-phase)
  cleanup.sh                      ← lab-level orchestrator: calls per-phase cleanups in
                                    reverse-DAG order, then handles cross-phase + lab-level state
  verify.sh                       ← lab-level verifier
  docs/
    attack_guide.md               ← chaptered for long labs
    concepts.md
    attack_narrative.md
    real_world_examples.md
    prerequisites.md
    defender_techniques.md        ← present only for incident-analysis blogs
  .cyberlab-gen/                  ← provenance, not for the user to edit
    blog_hash.txt
    generation_report.json
    validation_report.json
    refinement_history.json
    tool_version.txt
    iteration_snapshots/          ← retained top-3 by quality
                                    (safe to delete; lab runs without them)
```

The output stage is dumb: it moves files from the working directory to the user's target. All generation and validation happened earlier.

**Working directory cleanup.** On success, the working directory is cleaned up. On failure, the working directory is preserved at `~/.cyberlab-gen/runs/<run-id>/` for user inspection and possible resumption (§3.7).

**`validation-report.md` placement.** At the lab root, always generated. The user sees it immediately alongside `README.md`. The corresponding JSON version (`validation_report.json`) lives in `.cyberlab-gen/` for machine-readable use. This emitted verdict is **authoritative for downstream consumers**: the eval harness reads the pipeline's own mechanical verdicts (`eval.md §7.4`) — including the extract run's static-schema verdict carried in the run record — rather than re-running the validator outside the pipeline.

**`iteration_snapshots/` size.** Snapshots are full lab state. For large multi-phase labs, the snapshots directory can grow significantly. Users who want to slim labs can delete `.cyberlab-gen/iteration_snapshots/` after inspection without breaking the lab's runnability.

### 3.3 Cross-stage contracts

All inter-stage boundaries are typed Pydantic models. Free-text never crosses agent boundaries — with one channel exception: user-provided natural-language feedback at typed-artifact interrupts is wrapped in a structured `UserFeedback` object (with the free text in a designated field) before the agent sees it.

| From | To | Contract type |
|------|-----|--------------|
| Ingestion | Extractor | `IngestionResult` + cached blog content |
| Extractor | Pre-Planner enrichment | `AttackSpec` |
| Pre-Planner enrichment | Extractor-Jury | `AttackSpec` (enriched) |
| Extractor-Jury | Post-Extractor interrupt | `AttackSpec` (enriched, approved) |
| Post-Extractor interrupt | Planner | `AttackSpec` (approved) |
| Planner | Planner-Jury | `LabManifest` |
| Planner-Jury | Post-Planner interrupt | `LabManifest` (approved) |
| Post-Planner interrupt | Generator | `LabManifest` (approved) |
| Per-phase Generators | Lab-level Generator | working directory + `LabManifest` (in-progress) |
| Lab-level Generator | Cleanup Generator | working directory + manifest |
| Cleanup Generator | Docs Generator | working directory + manifest |
| Docs Generator | Validator | working directory + `LabManifest` |
| Validator | Critic | `ValidationReport` + working directory |
| Critic | Refinement coordinator | `QualityReport` + `ValidationReport` |
| Refinement coordinator | Generator (re-run) | targeted re-run instructions |
| Refinement coordinator | Output | validated lab + final reports |
| Output | (user filesystem) | lab directory |

Schema definitions: see `schema.md`.

### 3.4 Fix pipeline

A separate pipeline invoked by `cyberlab-gen fix <lab-dir>`. The Repair Agent (see `agents.md §5.16`) engages the user in an interactive REPL to debug runtime issues. Distinct from the generation pipeline; runs on already-generated labs that exist on disk.

#### 3.4.1 Shape

The fix pipeline has **one mode: interactive REPL**. There is no `--auto` — the whole point is conversation with the user. Autonomous fix would mean "patch my lab based on errors the system finds," which contradicts the principle that the user reviews every patch (`architecture.md §0.6`).

A `--apply-only` flag for non-interactive workflows (the user has already accepted patches and just wants them applied) is a v1.5+ deferral.

#### 3.4.2 Inputs and state

**Loaded at session startup.** The framework loads minimal startup context:

- `lab.yaml` (manifest).
- `.cyberlab-gen/generation_report.json` (summary; full content available via tool).
- `.cyberlab-gen/validation_report.json` (summary, including per-phase confidence).
- `.cyberlab-gen/fix_history.json` if prior sessions exist.
- The blog excerpts referenced by the manifest (small per-step).

**Continuity check.** When prior `fix_history.json` exists, the framework computes file hashes of the files referenced by prior fix_history entries and compares against the hashes the fix_history last saw. If any have changed (the user manually edited the lab between sessions), the framework marks prior history as "background context only — files have changed since last session" and surfaces this in the opening summary. The Repair Agent reads the marked history but treats it as historical context, not ground truth for the current state. This doesn't require new tools or regeneration; just an honest note about what's still true.

The Repair Agent has read-on-demand tools for everything else (`read_lab_file`, `list_lab_files`, `read_provenance`). The agent decides what to load based on the user's described problem. For small labs, the framework may preload all files as an optimization (transparent to the agent).

**During session.** Live conversation history is maintained in memory by the framework for the session's duration. Each user message triggers a new agent call (agents are stateless), but the framework includes the prior conversation as context — the same pattern as any chat with an LLM.

#### 3.4.3 Conversation flow

```
$ cyberlab-gen fix ./labs/my-lab
[framework loads minimal context, including fix_history.json if it exists]
[opening summary: phases, per-phase confidence, prior fixes if any]

> What's the problem?

[user pastes error + context]

[Repair Agent reasons; may web-search; may read additional files]

[Agent emits ONE of:]
  - propose_patch with diff           → user reviews → accepts (apply) /
                                         rejects (continue conversation) /
                                         asks for explanation
  - propose_doc_update                → similarly
  - explain_environmental_issue       → "this is your problem; do X"
  - request_more_info                 → user provides → agent continues

[on accepted patch: minimal validation runs; if validation flags
 something, the finding is surfaced back to the agent for revision;
 user sees both the revised patch and the validation finding]

[loop continues; user exits at any time with `exit` or Ctrl-D]

[fix_history.json is written incrementally during the session
 and definitively on exit]
```

**Session boundaries.** Same session = single REPL invocation. Cross-session continuity = new invocation reads `fix_history.json` and treats prior sessions as background context.

#### 3.4.4 Validation on proposed patches

Minimal validation only:
- **Static-schema validation**: runs if the manifest is touched.
- **Semantic cross-check**: runs on any change touching declared types or `references_lab_outputs`.
- **Safety scans**: run on every patch — patches shouldn't introduce credential exposure or host-attack patterns.
- **Containerized dry-run**: **auto-runs when the patch touches IaC files** (`.tf`, `.yaml` for CloudFormation/ARM, etc.). IaC patches can fail in ways static checks don't catch — terraform plan can surface issues that ruff/mypy/shellcheck cannot. For non-IaC patches (script-only, doc-only changes), the containerized dry-run is skipped by default and available via `--validate-patches-thoroughly` for users who want belt-and-suspenders.
- **Real-platform apply**: not applicable (v2-deferred even in generation; the user's own re-run of the patched lab is the real test).

If validation flags something, the finding is surfaced back to the Repair Agent as feedback. The agent revises the patch. The user sees both the revised patch and the validation finding that drove the revision.

#### 3.4.5 Budget

Separate budget from generation, separate cap. Default $5 LLM spend per fix session (placeholder pending eval-harness data), configurable via `--max-fix-cost` or config (`fix.max_cost`).

Budget-overrun behavior consistent with generation: when the next exchange's estimated cost would exceed the cap, the system surfaces a budget-overrun interrupt. User can raise cap, exit session, or proceed past cap explicitly.

Tracked per-session and cumulatively across-sessions. Cross-session tracking lets users see how much cumulative effort has gone into a given lab's fixes; useful for deciding when to regenerate from scratch instead.

#### 3.4.6 Persistence: `fix_history.json`

Each fix session appends to `.cyberlab-gen/fix_history.json` (inside the lab directory):
- Session timestamp.
- User messages (preserved as-is — the user consented by typing them).
- Agent responses (proposals, explanations, questions).
- Applied patches (with diffs).
- Validation findings on proposed patches.
- Final session state (resolved / suspended / etc.).
- Cumulative budget consumed across all sessions.

On subsequent invocations, the agent reads prior history for cross-session continuity.

#### 3.4.7 What the fix pipeline does not do

- **Run the lab.** The agent never executes the lab; only the user does.
- **Access cloud APIs.** The agent reasons about cloud problems from the user's pasted output, not by querying the cloud.
- **Network access beyond web search.** No reading the user's cloud account state, no SSH-ing into anything.
- **Regenerate.** If the user concludes a phase is fundamentally wrong, `fix` directs them to `cyberlab-gen generate` for that scenario; it doesn't regenerate from within `fix`. `--regenerate-phase` is v1.5+ deferred.
- **Branch back to upstream generation agents.** The Repair Agent is its own thing; it doesn't invoke Extractor, Planner, or generation Generators.
- **Cross-lab learning automatically.** Successful fix patterns stay in this lab's `fix_history.json`. Maintainer review can promote patterns to registry `notes_for_generator` updates or prompt overlays (manual process; automation is v1.5+ deferred).

### 3.5 Provider abstraction

LLM calls go through a `Provider` interface, not directly to a vendor SDK. v1 ships adapters for at minimum:

- Anthropic (Claude family).
- OpenAI (GPT family).

Local-model support (via Ollama or any OpenAI-compatible API) is designed-in but quality-tested deferred to v1.5+.

**Capability hints, not model names.** No agent code references a specific model. Agents request a capability hint (e.g., `high_quality_reasoning`, `fast_cheap_structured_output`, `long_context_extraction`). The provider layer maintains a ranked preference per capability across providers and models. The user's configured providers determine which entries in the ranking are reachable. The provider layer picks the highest-ranked reachable model.

Adding a new model to the system is a ranking-file edit: edit one ranking file, ship a release. Agent code does not change.

**Base prompts plus model-specific overlays.** Each agent has a base prompt (model-agnostic) and optional model-specific overlay files (tweaks for known model-specific failure modes). Adding a new model means writing one overlay if needed, not rewriting the agent.

**Per-model cost tracking.** The provider layer tracks token usage and cost per model, not just per agent. The framework converts token counts to spend using the provider's published pricing for the active model. The `--max-llm-cost` cap is in dollars; per-agent budgets are in tokens; the framework reconciles. This makes cross-model cost comparisons honest and feeds the eval harness's cost-per-quality metric.

**Structured-output enforcement at the boundary.** Provider responses are parsed against the agent's declared output schema. Malformed responses are retried (counted against retry budget per §3.7). After retry exhaustion, treated as agent failure.

**Multi-model jury support.** The jury layer can specify a different provider/model than the agent it judges, drawing from the capability ranking. If only one provider is configured, the jury uses a different model from the same provider (degraded diversity). "Logged" here means: the run report's jury section records `multi_model_diversity: degraded_same_provider`. Not telemetry-triggering, not a CLI warning. The eval harness measures whether multi-model juries actually reduce false-approval and false-rejection rates.

**No provider configured.** Startup fails with a clear message pointing to configuration documentation. At least one provider key is required to run the tool. (The user provides a key for the LLM provider only; external API keys for NVD/GitHub etc. are optional optimizations — see §3.6.1.)

**Recommended capability mappings ship in the ranking file.** The architecture document does not pin specific model names; the bundled ranking file ships with current recommendations and is updated per release. Users may override via config.

### 3.6 Telemetry (local-first, transparent submission)

**No background phoning home.** Every run writes a structured report to `~/.cyberlab-gen/reports/<timestamp>.json`. The user can inspect, modify, or delete these freely. The local report is *high-fidelity* — it contains everything that happened during the run, useful for the user's own debugging and for telemetry submission to maintainers.

#### 3.6.1 Credentials in cyberlab-gen

Three distinct types of credentials, used by different actors at different times:

| Credential type | When needed | Required? | Where configured |
|---|---|---|---|
| LLM provider API key (Anthropic / OpenAI / etc.) | Generation time | Yes, at least one | `~/.cyberlab-gen/config.yaml` or env vars |
| External data source API keys (NVD, GitHub PAT, etc.) | Generation time (rate-limit relief) | No — optional optimization | `~/.cyberlab-gen/config.yaml` or env vars |
| Cloud credentials (AWS / Azure / GCP / GitHub for lab targeting) | Lab `setup.sh` run time (not used by the tool during generation in v1; the real-platform apply pass is v2-deferred) | No for generation; yes for running the lab | Standard tooling conventions (`~/.aws/credentials`, `az login`, `gcloud auth`, `gh auth login`); the tool never touches them |

For generation, the user needs **one LLM provider key**. Everything else is optional or run-time. External API keys raise rate limits but generation works without them — the system runs on no-auth tiers by default. Cloud credentials are checked by the *generated lab's* `prereqs.pre_lab` at run time using standard cloud-CLI conventions; cyberlab-gen never reads them.

#### 3.6.2 Submission

A separate CLI subcommand sends queued reports:

```
cyberlab-gen telemetry submit
```

This command:

1. Lists how many queued reports exist.
2. Runs a **sanitization pass** that strips sensitive content (see §3.6.4 below).
3. Shows the user a side-by-side diff of the raw report and the sanitized version.
4. Asks confirmation.
5. Sends to the project's endpoint if and when one exists.
6. Optionally clears the queue on success (`--clear`).

If the endpoint does not exist (e.g., user is on an early version, or project has no endpoint operational yet), the command says so and points to manual sharing instructions (open a GitHub issue, attach files).

**Power users can automate** with `cyberlab-gen telemetry submit --yes --no-confirm`, skipping the sanitization-diff confirmation. Recommended only for users who trust their own setup and have reviewed what's collected.

**Opt-out.** Users can disable local report writing entirely via `--no-telemetry` flag or `telemetry.enabled: false` in config. This disables the `telemetry submit` workflow but doesn't affect generation. Recommended for users in regulated environments who can't have any logs generated.

#### 3.6.3 What is collected

When the user submits, the sanitized report contains:

- **Tool metadata.** Version, OS, Python version, configured providers and models.
- **The blog.** Full URL. The blog is public; sending its URL doesn't compromise anyone.
- **Pipeline trace.** Per-stage timing, retries with causes, refinement iteration causality log.
- **Per-agent / per-model token counts and costs.**
- **All artifacts produced by the run.** AttackSpec, LabManifest, generated lab content (Terraform, scripts, docs). All derived from the public blog.
- **Validation results.** Per layer with specific findings; Critic verdict, per-phase confidence, and concerns.
- **Outcome.** (success / partial / abandon) and cause.
- **Registry state.** Entries used, proposals made (with full content), user disposition at interrupts (accept/edit).
- **Failure details.** Exact error, stage, failure mode, stack traces.
- **User feedback text at interrupts.** The user's own input; they consented by typing it.
- **Fix-session history.** Aggregated from `fix_history.json` if the lab has had fix sessions (after sanitization).

#### 3.6.4 What is never collected (sanitization strips these before sending)

- **API keys, provider tokens, cloud credentials, any auth material.**
- **Cloud account identifiers** (AWS account IDs, Azure tenant/subscription IDs, GCP project IDs).
- **User-specific resource identifiers from real-platform apply runs** (real ARNs, real resource names, real IPs from the user's cloud) — relevant in v2 when the real-platform apply pass returns; in v1 these don't exist.
- **Absolute file paths from the user's machine** (home-directory components redacted; relative paths from the working directory retained).
- **Anything in the working directory that wasn't generated by cyberlab-gen.**

**Sanitization scope includes fix_history.json content.** The local `fix_history.json` preserves user-typed conversation as-is (the user consented by typing it), but submission sanitization runs over the fix_history content too — users will paste sensitive output (errors with credentials, stack traces with cloud account IDs) into fix sessions, and the locally-preserved-as-is property makes the submission sanitization pass critical.

**Sanitization is best-effort with user confirmation as final guard, not cryptographic.** Pattern matching catches known credential formats (AWS access keys, GitHub PATs, etc.); novel formats may slip through. The user-visible diff at submission time is the safety net — if redactions don't satisfy the user, they choose not to send. The pattern set is documented in the planned `telemetry.md` companion.

The local report (kept on disk) is full-fidelity for the user's own use. The submission sanitization runs only at send time. The user sees the diff and can choose not to send any report whose redactions don't satisfy them.

#### 3.6.5 Endpoint-side hardening (if/when the endpoint exists)

- Treat all submissions as untrusted. Server-side schema validation; reject malformed payloads.
- Per-IP rate limiting.
- Median/percentile aggregation for statistical use; full-payload retention for failure debugging.
- Manual review of outlier patterns before trusting them as signal.
- Standard web service hardening: payload size limits, no arbitrary deserialization, no execution of payload content.

A `telemetry.md` doc in the repo explains exactly what's collected, what's redacted, what's done with the data, and how to opt out. Linked from README.

### 3.7 Provider failure handling

Provider calls can fail in several ways. The pipeline handles each as follows.

**Transient failures (timeout, transient 5xx).** Retry with exponential backoff, up to 3 attempts. Counts against the agent's per-stage retry budget but not against the refinement budget. (Per `architecture.md §1.1`, retry and refinement are distinct mechanisms.)

**Rate-limit (429).** Same retry strategy as transient. After exhausting retries, treated as a temporary outage — see below.

**Quota exceeded.** Hard fail with clear error indicating which provider, what was exceeded, and what the user can do (raise quota, switch provider, retry later). Pipeline writes a checkpoint (see below) so the user can resume.

**Malformed structured output.** The provider returned text that doesn't parse against the declared schema. This is handled at two layers — see `provider-interface.md §6.2` for the provider side and ADR 0018 for the resolution; the two sections describe the same model and do not conflict. First, the provider re-prompts the model internally (up to 2 attempts — initial + 1 retry) and on exhaustion raises `MalformedOutput`; that provider-internal count is *not* charged to the stage retry budget. That single `MalformedOutput` then surfaces as **one** structural failure of the stage, which the agent call surface may itself retry against the stage's structural-retry budget — this is the budget "counted against" here. When the stage budget is exhausted it is treated as agent failure and routed to refinement-or-abandon per §3.2.12. (Per `architecture.md §1.7`, this structural retry is distinct from refinement.)

**Mid-pipeline provider outage.** When a provider call fails after retries are exhausted, the pipeline writes a checkpoint to `~/.cyberlab-gen/checkpoints/<run-id>/` containing:

- All completed artifacts up to the failure point.
- The pipeline state (which stage was running, what input it had).
- The failure cause.

The user can resume with `cyberlab-gen resume <run-id>`. Resume requires the same tool version and same schema version as when the checkpoint was written; otherwise refuses with a clear "regenerate from scratch" message (consistent with `architecture.md §0.6`'s no-migration stance).

**Checkpoint-write failure.** Disk-full, permissions failure, or other I/O error during checkpoint write is treated as a hard fail: no resume possible, partial output preserved in the working directory at `~/.cyberlab-gen/runs/<run-id>/`, structured diagnostic written. The user gets the same hard-fail handling as the "no checkpoint possible" path below.

**Hard fail mid-run with no checkpoint possible.** Partial output is preserved in the working directory at `~/.cyberlab-gen/runs/<run-id>/` for user diagnosis. The user does *not* receive this as a shipped lab — failure here means no lab ships, only a diagnostic report. (This is distinct from §3.2.12's "ship with low-confidence flag" outcome on budget exhaustion; that path produces a shippable lab. A hard provider failure produces no lab.)

**Persistence authority: the run store.** The two on-disk systems above — the run store (`~/.cyberlab-gen/runs/<run-id>/`, the system's memory of what a run produced) and the LangGraph checkpointer (`~/.cyberlab-gen/checkpoints/<run-id>/`, resumable completed-node state) — are **not** two independent "remember the partial run" mechanisms. The **run store is the single persistence authority**: on every exit, clean or aborted, it records what the run produced by reading the checkpoint (the completed-node state) or the graph's last-emitted state **directly** — not an in-memory field populated only on a clean graph return (which a mid-graph abort leaves empty, dropping the partial run even though the checkpoint holds it). The checkpointer's job stays narrow — persist super-step state so `resume` can continue — and the run store reads from that same state, so a partial run is captured once, by one authority. See ADR 0053.

**What this stage does not do.**

- **Automatically switch providers mid-run.** Switching providers can change behavior in subtle ways; the pipeline never makes that decision silently. The user can change their config and resume.

---

*End of pipeline document. See `agents.md` for component contracts within each stage, `schema.md` for artifact shapes, `validation.md` for validator layer details, and `eval.md` for the eval harness.*
