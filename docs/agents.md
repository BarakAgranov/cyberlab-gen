# cyberlab-gen — Agent Designs

**Companion to:** `architecture.md` (hub).
**Document scope:** Contracts for each agent in the system — what inputs it receives, what artifact it produces, what tools it has access to, what its quality bar is, and how it interacts with juries and framework code around it. Does not specify exact prompts (those live in implementation companion docs); specifies the shape of the prompt's job.

---

## 5. Agent designs

### 5.1 What this section covers

This section specifies the **contracts** of each agent in the pipelines. It does not specify exact prompts (those live in `prompts.md`, planned companion); it specifies the shape of the prompt's job.

The system has **ten agents** plus three jury invocations plus framework code that runs deterministically between agents. Agents only do what requires LLM reasoning. Everything else is code.

### 5.2 The agent inventory

The system has two pipelines (`pipeline.md §3`):

**Generation pipeline agents (1–9):**

1. Extractor
2. Extractor-Jury (jury, conditional)
3. Planner
4. Planner-Jury (jury, conditional)
5. Per-phase Generator (parallelized over phases)
6. Lab-level Generator
7. Cleanup Generator
8. Docs Generator
9. Critic

**Fix pipeline agents:**

10. Repair Agent (per `pipeline.md §3.4`)

**Framework stages (deterministic, no LLM):**

- Ingestion (fetches blog, computes content hash, normalizes encoding)
- Pre-Planner enrichment (runs `enrichment_triggers` from external sources registry)
- Post-Generator file emission (writes generated artifacts to disk)
- Validator (the mechanical layers from `validation.md`)
- Refinement loop coordinator (decides whether to re-run a stage)
- Output assembly (composes final lab directory)

The split between agent and framework is deliberate (per `architecture.md §1.5`): agents reason about content; framework enforces structure, runs deterministic operations, and orchestrates control flow. The control flow itself never delegates to an LLM.

**Agent label enum.** The closed set of agents above is also exposed as `AgentLabel: StrEnum` in `cyberlab_gen.providers` (see `provider-interface.md §4.1`) for cost-ledger attribution and by-agent reporting. The enum values match the agent names listed here in `snake_case` form (e.g., `EXTRACTOR = "extractor"`, `EXTRACTOR_JURY = "extractor_jury"`, `PER_PHASE_GENERATOR = "per_phase_generator"`). A typo on a free-form string would silently break by-agent rollups in the run report and eval harness; the closed enum makes that mistake impossible.

### 5.3 Agent contract template

Every agent in this section is specified using the same template:

- **Job** — one sentence describing what the agent does.
- **Inputs** — the structured artifacts it receives.
- **Output** — the structured artifact it produces.
- **Tools** — what external capabilities it has access to.
- **Provenance discipline** — how it must handle the source/citation/confidence pattern.
- **Quality bar** — what counts as a successful run.
- **Failure modes** — what can go wrong, how the framework responds.
- **Notes** — anything else worth recording.

Where agents have substantial subcontent, it appears in subsections.

### 5.4 Extractor

**Job.** Read the source blog and produce a structured AttackSpec describing what the blog says happened.

**Inputs.**

- Normalized blog content (text, possibly with structural markers like headings preserved).
- Source metadata from Ingestion (URL, fetched-at, content hash, publisher).
- The current schemas for AttackSpec, value_types registry, facets registry.
- The external_data_sources registry (for tool calls).

**Output.** A complete AttackSpec YAML artifact, conforming to the AttackSpec schema (`schema.md §4.8`), with provenance metadata on every content field.

**Tools.**

- Search and lookup tools against the external_data_sources registry (NVD, MSRC, MITRE ATT&CK, OSV, GitHub API, etc.) via the generic `external_lookup(source_id, params)` tool.
- A `propose_value_type` tool for emitting LLM-proposed registry entries when the blog mentions a typed value with no existing registry match.
- A `propose_facet` tool for proposing new `target:*` or blog-derived `lab_class_signal:*` facets (rare; most facets already exist for v1). Runtime facets (`runtime:*`) and lab-derived `lab_class_signal:*` are proposed by the Planner, not the Extractor (see `schema.md §4.16`).
- A `propose_external_source_pattern` tool surfaces "this blog references an external source we don't have in the registry" — surfaced for maintainer PR review, not auto-added.

The Extractor cannot read the file system, cannot execute code, cannot fetch URLs other than through the external_data_sources tool interface. It is read-only against the blog and against authoritative external APIs.

**Provenance discipline.** Provenance is categorical (per `schema.md §4.20`): the source is what actually produced the value, not a preference. The Extractor sets `source: blog_explicit` when the blog directly states the value; `source: llm_inference` when the schema field needs filling and the blog implies (rather than states) the answer; `source: unknown_from_blog` when neither applies. The Extractor never invents context the blog didn't establish; inference is allowed but must be marked and cited.

The Extractor must execute a real search call against external sources for any inferred CVE, technique mapping, or advisory reference. Pure recall is not allowed (the "search-before-claim" pattern from `schema.md §4.15`). These are **mechanical checks the orchestrator owns and routes** (`validation.md §6.10.2`), not an Extractor-internal loop: the framework rejects an AttackSpec whose `external_api` fields lack matching tool-call records in the trace, and the *orchestrator* — not the Extractor — decides whether to re-run it. The Extractor produces content; it does not own a validation-retry budget (`architecture.md §1.5`).

**Decision tree for typed values** (per `schema.md §4.10`): the Extractor's prompt includes the discipline for choosing among existing type, propose new, or `extras` (for non-value content). The Extractor is the only agent that proposes new value types; if it puts a value in `extras` when it should have proposed a value type (or vice versa), the Extractor-Jury flags and the Extractor re-runs.

**Quality bar.**

- AttackSpec validates structurally against the schema.
- All required structural fields populated.
- Every content field has provenance.
- Completeness score above the configured floor. **Completeness score** is defined as the fraction of content fields populated with non-`unknown_from_blog` provenance. Default floor: 0.5 (v1 placeholder pending eval-harness data per `architecture.md §8.4`).
- Granularity follows the blog's narrative — no over-decomposition or under-decomposition. (The Planner re-decomposes for implementation later; the Extractor mirrors the blog.)

**Failure modes.**

- Blog is unreadable or paywalled → Ingestion catches this; Extractor never sees it.
- Blog is too short or too vague to extract a chain → Extractor produces a low-completeness AttackSpec with `extras.extraction_warning` populated. Critic flags it later.
- Blog describes a non-cloud-relevant attack → Extractor sets `extraction_outcome: out_of_scope` with reason at the top level of the AttackSpec (per `schema.md §4.8`). The framework emits the out-of-scope notice (halts in `--auto` per `pipeline.md §3.1.1`).
- External API call fails → Extractor records the failure in the field's provenance, leaves the field as `unknown_from_blog` with the API failure noted. Pipeline continues.

**Notes.**

- The Extractor sees the entire blog at once (with chunking only if length exceeds context window). It does not do progressive extraction; the AttackSpec is produced in one pass, then refined by the jury via **targeted patch** — on a `revise`, the Extractor edits only the flagged fields rather than re-extracting (`architecture.md §1.7`). The chunking strategy and reconciliation logic live in implementation; long blogs are a real v1 concern with the eval harness including long-blog cases.
- The Extractor populates `chain_step_excerpts` — per-step quoted blog passages — that flow downstream so other agents can see source text without re-reading the whole blog. The Planner re-keys these into per-phase excerpt bundles.
- Scope decisions vs. planning decisions: the Extractor decides *whether the content is in cyberlab-gen's scope at all*. The Planner decides *whether a coherent lab can be planned given user configuration, registry coverage, and reproducibility ladder*. Two different "should this become a lab" questions, separated by responsibility.

### 5.5 Extractor-Jury

**Job.** Review the AttackSpec for fidelity, completeness, and provenance correctness. Propose refinements.

**Inputs.**

- The AttackSpec produced by the Extractor.
- The original blog content (for fidelity checks).
- The Extractor's tool call trace (for provenance verification).
- The mechanical-validator stack's findings set (`validation.md §6.10.2`) — the jury **consumes** these (static-schema, provenance-structure, grounding) and does not re-derive them, mirroring how the Critic reads the Validator report without re-checking (`§5.14`).

**Output.** A structured verdict (`approve` / `revise` with feedback / `reject` with reason).

The jury produces a judgment; the framework reads the verdict and decides what to do: `approve` → continue; `revise` → the framework hands the Extractor the prior AttackSpec plus the jury's *structured*, field-level feedback, and the Extractor returns a **patch** for only the flagged fields (counts against refinement budget; see `architecture.md §1.7` and `schema.md §4.9`); `reject` → pipeline halts with explanation.

**Tools.** A **verify-only** subset of the Extractor's tools (ADR 0078): the read/verify external-source lookups (`external_lookup`) to independently check API responses, but **no `propose_*`** tools — proposal authorship belongs to the producer (the Extractor), not the jury. The jury flags missing or wrong proposals; it does not make them. (See the §5.18 tool matrix, which is the authoritative split.)

**Provenance discipline.** The *mechanical* half — that an `external_api` field has matching tool-call evidence in the trace at all — is the stack's provenance-structure layer (`validation.md §6.10.2`); the jury consumes that and adds the *semantic* half it is uniquely for, verifying that each `source` claim actually holds:

- For `blog_explicit`: does the cited passage actually say what the field claims?
- For `external_api`: does the cited API response actually contain that value?
- For `llm_inference`: is the reasoning trace coherent? Are the cited passages relevant?

The jury rejects fields where provenance doesn't match the claim.

**Quality bar.**

- The jury runs in two configurations: single-model jury (default) and multi-model jury (when the user has multiple providers configured). Multi-model juries provide stronger signal but cost more.
- A jury run produces a rubric score across dimensions: fidelity to blog, completeness, provenance correctness, structural validity. Each dimension scored 0–1.
- An AttackSpec passes if all dimensions score above their floor. Default 0.7 (v1 placeholder pending eval-harness data per `architecture.md §8.4`).

**Threshold and retry calibration.** The 0.7 floor and the N=2 retry count are tunable defaults. The eval harness measures false-approval rate (jury approved an AttackSpec that the held-out reference says was wrong) and false-rejection rate (jury demanded revisions on an AttackSpec that the held-out reference says was correct).

**Asymmetric calibration is mandatory.** For cyberlab-gen, false-approval is costlier than false-rejection because bad AttackSpec cascades through every downstream stage. Threshold calibration discipline: tune *upward* on observed false-approval (tightening), do not symmetrically tune downward on observed false-rejection (loosening). The eval harness can drive both directions algorithmically; the calibration discipline overrides by intentionally privileging stricter approval over jury throughput. See `eval.md §7.5` for the calibration mechanism.

**Failure modes.**

- **Jury identifies provenance fraud in individual fields (1–3 content fields with mismatched citations) → `revise` verdict with field-specific feedback.** The Extractor receives the prior AttackSpec plus targeted field-level feedback and returns a **patch** for those specific fields; the framework deep-sets it and re-validates, leaving unflagged fields untouched (`architecture.md §1.7`). The pipeline does not halt.
- **Jury identifies provenance fraud systematic across many fields (>30% of content fields with mismatched citations) → `reject` verdict.** This indicates the Extractor was operating in a fundamentally broken mode (cascading hallucination); the pipeline halts with the rejection reason in the run report.
- **Disagreement handling.** When juries disagree (multi-model split) or retries are exhausted: in `--interactive`, escalate to user with both opinions surfaced; in `--auto`, accept the lower-scoring assessment as conservative default. When retries are exhausted, distinguish two outcomes:
  - (a) jury verdict is `reject` with a fundamental concern → halt;
  - (b) jury verdict is `revise` with the same feedback unresolved → proceed with the last AttackSpec carrying a `low_jury_confidence` flag and the unresolved feedback in the run report. Better to ship a partially-reviewed AttackSpec with explicit "low jury confidence" than to spin until budget exhausted.
- Jury exhausts its tool budget → produces a partial review. Framework treats partial reviews as advisory, not blocking.

**Notes.**

- The jury exists at two points: post-Extractor (this), post-Planner. The Critic (§5.14) is a separate role, not a jury — it runs after generation against the complete lab.

### 5.6 Pre-Planner enrichment (framework, not agent)

Specified in `pipeline.md §3.2.4`; not repeated here. Brief: deterministic framework pass over the AttackSpec running `enrichment_triggers` from external_data_sources. Mandatory; never delegated to an agent. The framework rewrites fields with `source: external_api` when API findings contradict blog content (framework-only-authorship per `schema.md §4.9`); rate-limited mandatory enrichment is recorded with `unknown_from_blog.reason` per `schema.md §4.14`.

### 5.7 Planner

**Job.** Consume an enriched AttackSpec and produce a draft lab manifest — deciding which chain steps become phases, which become steps within phases, which become lab resources, which become prerequisites (pre_lab or mid_lab), which become demonstration-only or get dropped.

**Inputs.**

- The enriched AttackSpec.
- The current schemas for the lab manifest, value_types registry, facets registry.
- The user's optional preferences (e.g., `preferred_clouds`, if set in config) — informational, may influence which platforms the Planner prioritizes when the AttackSpec leaves the choice open. **Not used as a capability gate.** Lab-run-time credentials are checked by the generated lab's `prereqs.pre_lab`, not at planning time (per `pipeline.md §3.6.1`).

**Output.** A draft lab manifest with:

- All structural fields populated (phases, lab_resources, prereqs split into pre_lab/mid_lab, inputs, outputs).
- `phases` populated with steps but without `implementation.path` (no code generated yet).
- `produces_world_state` declarations per phase.
- Facets declared.
- `lab_resources` declared with type, identifier, **intended IaC resource type**, and **`lab_role`** list (per `schema.md §4.4`). The Planner assigns lab_role values per resource — `attack_target`, `attacker_infrastructure`, `defender_infrastructure`, or `neutral` — based on what the resource is doing in the lab. A single resource can have multiple roles (e.g., a logging bucket the attack deletes from is `[defender_infrastructure, attack_target]`). When the role is `attack_target`, the containerized dry-run relaxes security-finding strictness for that specific resource (see `validation.md §6.6`). The Lab-level Generator translates lab_resources into actual IaC code per §5.11.
- Per-step `reproducibility` carried forward from the AttackSpec at the **step level** (per `architecture.md §0.7`'s emergent-class principle); lab-level `reproducibility` derived per `schema.md §4.8`.
- Re-keyed per-phase excerpt bundles from the AttackSpec's chain_step_excerpts.
- No actual code, IaC, or docs.

**Cost and runtime estimates are framework-computed, not Planner output.** After the Planner runs, the framework uses the Planner's output size and downstream-stage cost models to refine the estimate from the AttackSpec-stage estimate. The Planner does not emit cost estimates; consistent with `architecture.md §1.5` (LLMs don't compute their own behavior; framework does).

**Tools.**

- The same external_data_sources tools as the Extractor (the Planner may need additional lookups during planning).
- A `propose_facet` tool, scoped to **`runtime:*` facets (lab-derived) and lab-derived `lab_class_signal:*` facets** (e.g., `simulated_components`, `multi_language`, `parameterized`) when a property emerges from lab structure that the registry doesn't yet name. See `schema.md §4.13` for the authorship split.
- The Planner does **not** propose value types — that authority is the Extractor's alone (§5.4). If the Planner finds itself needing a value type that's not in the AttackSpec, that's a signal the Extractor missed something; the Planner-Jury flags this and the refinement loop routes back to the Extractor.
- A `query_value_types_registry` tool for finding existing typed shapes that match planned outputs.

**Provenance discipline.** The Planner inherits the AttackSpec's provenance and may add its own:

- Decisions like "these three blog steps become one phase" are Planner inferences about how to structure the AttackSpec into an implementation. They are recorded with `source: llm_inference`, with the AttackSpec chain steps cited and the Planner's reasoning as the inference trace.
- Decisions about `step_composition` (sequential vs independent), `execution_context`, `provisioning_mechanism`, and `on_dependency_failure` are Planner inferences with the same `source: llm_inference` shape.
- The Planner does not invent content; it organizes and structures content the AttackSpec already established. New content fields the Planner creates (e.g., a phase's `short_description`) carry `source: llm_inference` with citations into the AttackSpec's chain steps that grounded the inference.

**Lab class is emergent (per `architecture.md §0.7`).** The Planner does not pre-classify the lab into a shape, and it does not re-apply the §4.20 reproducibility ladder — the per-step tier was assigned by the Extractor and is carried forward *unchanged* (`§0.7`). Working at the step level, the Planner decides how each carried-forward step is realized — a phase, a step within a phase, a lab resource, a prereq, or dropped when the tier is `not_reproducible` — and records that rationale in the manifest. The lab's overall shape is the result of these per-step tiers plus the Planner's structural decomposition; phase shape emerges from the mix.

**Missing-value-type routing.** Missing value types are detected at two points:

- At planning (Planner notices an AttackSpec value lacks a type; routes back to Extractor via refinement).
- At generation (Per-phase Generator notices a manifest field references a non-existent type; routes back to Planner, which may further route to Extractor if the type wasn't in the AttackSpec).

**Quality bar.**

- Manifest validates structurally.
- Every phase declared with required fields.
- All input types reference value_types registry entries that the Extractor either found or proposed. The Planner never falls back to untyped values.
- No phase has a circular dependency on another.
- Reproducibility classifications honored: `not_reproducible` chain steps are dropped (never silently upgraded — the Planner does not re-tier, `§0.7`); `demonstration_only` chain steps become demonstration-only phases; `partial_simulation` chain steps may become real phases with `lab_class_signal:simulated_components` declared.

**Failure modes.**

- AttackSpec gaps too large to plan around → Planner refuses with `cannot_plan` error in both modes. Gaps are an AttackSpec-level concern; the user fixes them by re-running with Extractor feedback at the post-Extractor interrupt, not at the post-Planner interrupt.
- AttackSpec implies infrastructure the system cannot express as code → Planner refuses with `cannot_plan` error and structured reason. Rare with the open-runtime model (`schema.md §4.13`); typically the reproducibility ladder drops individual steps instead. Only when *no* meaningful lab can be constructed does the Planner refuse outright. **Credentials are NOT a planning concern** — credentials, regional configuration, and per-platform tooling are handled by the generated lab's `prereqs.pre_lab` checks at run time.
- AttackSpec is incoherent in a way the Extractor missed (steps with mismatched preconditions/postconditions) → Planner flags with structured detail; the refinement loop routes back to the Extractor. **The Planner does not repair AttackSpec content.** The fact that the Planner can see the incoherence doesn't grant authority to fix it. AttackSpec authorship is the Extractor's responsibility per §5.20's ownership rules; any other agent repairing AttackSpec would create an exception that erodes the framework-only-authorship discipline.
- Planner cannot decide between two reasonable phase decompositions → emits both options with rationale; pipeline pauses at the post-Planner interrupt for user choice in interactive mode.

**Notes.**

- The Planner is the most decision-dense stage. It operates against the largest schema (the manifest) with the richest input (an enriched AttackSpec).
- The Planner does not write code or IaC. The output is a "skeleton" manifest with all metadata and structure but no implementation.
- **No fixed phase count.** Phases are however many the chain has, after the Planner's grouping. Long blogs produce long labs; the architecture supports them via chaptered docs (§5.13) and `--from-phase` setup (§5.11).

**Global decisions vs. lab-class assignment.** The Planner makes decisions that span the whole lab — phase decomposition, facet declarations, and the lab-level reproducibility the framework derives from the carried-forward per-step tiers. These are global decisions but not lab-class assignments.

The architecture deliberately rejects the notion of *a* lab class. A lab can be multiple things at once: a vulnerability disclosure that's also an incident analysis, a supply-chain compromise that demonstrates a cross-tenant capability, a misconfiguration with a privilege-escalation chain. The thesis `types` field is a list precisely because labs are multi-typed in practice (`schema.md §4.8`).

What the Planner does *not* do is assign a master classification that downstream agents key behavior off of. There is no "if lab is class X, generate this kind of artifact." There is "for each chain step, carry forward its Extractor-assigned reproducibility tier unchanged and decide its structural realization; for each phase, derive composition from declared inputs/outputs; for each facet, declare based on what the manifest's content implies." Behavior emerges from per-step and per-phase decisions, not from a top-level type or class label.

The `thesis.types` list is *descriptive* (this lab matches these types) not *prescriptive* (downstream behavior is determined by these types). Generators read step composition, execution context, declared facets, and per-step reproducibility; they do not read `thesis.types` as a behavior switch.

### 5.8 Planner-Jury

**Job.** Review the draft manifest for fidelity to AttackSpec, structural validity, and reasonableness of planning decisions.

**Inputs.**

- The draft manifest.
- The enriched AttackSpec.
- The Planner's reasoning trace.

**Output.** Approval or refinement request, same shape as Extractor-Jury.

**Tools.** A **verify-only** subset of the Planner's tools (ADR 0078): read/verify external-source lookups to check the Planner's external_api findings, but **no `propose_*`** tools. The jury flags facet/value-type proposal gaps; it does not propose. (See the §5.18 tool matrix.)

**Provenance discipline.** Verifies Planner decisions trace to AttackSpec content. Phases must be derivable from chain steps; lab_resources must be implied by chain preconditions or explicit blog mentions; prereqs must be sourced from blog or framework defaults.

**Facet review scope.** The Planner-Jury reviews facets declared *by the Planner* — `runtime:*` and lab-derived `lab_class_signal:*` (per §5.7). Facets inherited from the AttackSpec (`target:*` and blog-derived `lab_class_signal:*`) were reviewed by the Extractor-Jury and are taken as-is. The Planner-Jury also reviews the manifest fields that *use* any `runtime:*` or lab-derived `lab_class_signal:*` facet the Planner proposed — a proposed facet is justified (or not) by the spec that uses it, the same way the Extractor-Jury covers value-type proposals through spec review. Per-proposal **Accept/Edit** is the user's interrupt menu (`schema.md §4.16` stage 2); overlap/dedup against existing entries is the mechanical merge-check at promotion time (`schema.md §4.16` stage 4), not a jury gate.

**Quality bar.**

- Manifest covers the full AttackSpec without orphaning chain steps.
- Phase decomposition is reasonable (not 1 phase, not 20 phases for a small lab — though 20 phases is fine if the chain has 20 distinct actions).
- Facets declared by the Planner match what the manifest fields imply.
- No undeclared dependencies between phases.
- Demonstration-only phases correctly marked.
- Per-step reproducibility correctly preserved from AttackSpec; lab-level reproducibility correctly derived per the any-heterogeneity-mixed rule from `schema.md §4.8`.
- Fallback decisions per `schema.md §4.20` are documented honestly (no shortcut to demonstration-only when full was achievable). At LabManifest level — Generator-level fallbacks are reviewed by the Critic in §5.14.

**Failure modes.**

- Planner produced a manifest that drops important AttackSpec content (e.g., an entire credential-harvest stage missing) → reject with diagnostic.
- Planner over-decomposed (every chain step became its own phase when grouping was natural) or under-decomposed (everything in one phase when grouping was unnatural) → refinement request with target granularity.
- Same disagreement-without-progress handling as Extractor-Jury (§5.5): exhausted retries with `revise` verdicts → proceed with `low_jury_confidence` flag; exhausted retries with `reject` verdict → halt.

**Notes.**

- After Planner-Jury approval, the manifest enters the user-interactive interrupt point (post-Planner) in interactive mode. User reviews the manifest via the four-option menu (`pipeline.md §3.2.8`) and any facet proposals via Accept/Edit per `schema.md §4.16`.

### 5.9 Per-phase Generator

**Job.** Generate the implementation of one phase: the Python module, optional Terraform (or other IaC) per phase, the inline detection emissions, the step-by-step CLI equivalents.

This is the **agent that runs in parallel** — one instance per phase, invoked by the framework after the manifest is finalized. Each instance sees only its phase plus shared context.

**Inputs.**

- The phase block from the manifest, fully specified.
- The lab's `core` block (for context).
- The phase's blog excerpts (the relevant slice of the blog, re-keyed from chain_step_excerpts by the Planner).
- The value_types entries for all types this phase consumes or produces.
- The facets declared on this phase.
- The names of any lab-level Terraform outputs this phase has declared it will reference (per `references_lab_outputs` in the manifest), but not the Terraform code itself. **Lab-level Terraform output names were declared by the Planner in the manifest's `lab_resources` block; the Lab-level Generator is contractually bound to produce outputs matching those declared names.** The Validator's semantic cross-check verifies this contract (`validation.md §6.5`).
- Canonical code-shape examples for the phase's `step_composition` × `execution_context` combination (provided as prompt examples, not constraints — see §5.10).
- The canonical lab-credentials catalog (read-only; for planting fakes that pass detection-scanner heuristics — see `validation.md §6.8`).

**Output.** A directory of files implementing the phase:

- `attack/<phase_id>/attack.py` (or `.sh`, `.js`, etc., per declared language) — the entry-point with `run_phase(config) -> dict`.
- `attack/<phase_id>/infra/main.tf` — phase-specific Terraform if the phase has its own infrastructure (or equivalent for other declared `provisioning_mechanism`).
- `attack/<phase_id>/cleanup.sh` — per-phase cleanup script for state this phase creates that persists beyond phase execution. Idempotent. Targets specifically what this phase did, not the whole lab.
- Any payload files declared in the phase (e.g., a malicious Lambda payload).

**Why per-phase cleanup is owned by the phase agent.** The agent writing the attack code has the freshest context for what state was created — exact resource names, exact temporary file paths, exact identifiers (often randomized at runtime). Asking a downstream agent to reverse-engineer "what did phase 4 create?" from declarations is strictly worse than asking phase 4's author to write its own cleanup. Cross-phase state and ordering — things this phase doesn't own — go in the lab-level `cleanup.sh` written by the Cleanup Generator (§5.12).

The per-phase cleanup script's scope:

- **In scope:** state created *by this phase's code* — IAM users created, files written, env vars set, ephemeral resources spun up via CLI. Anything in this phase's `produces_world_state` that's owned by the phase rather than by lab-level setup.
- **Out of scope:** lab-level resources (those die with `terraform destroy`), state shared across phases (handled by lab-level cleanup orchestrator), state that other phases also modify.

**Tools.**

- Read access to the manifest.
- Read access to relevant value_types entries and their `notes_for_generator` fields.
- A `lookup_cloud_iam_action(cloud, action)` tool that consults the appropriate static catalog (AWS IAM, Azure RBAC, GCP IAM permissions per `schema.md §4.14`) based on the `cloud` parameter, for hallucination prevention across all first-class clouds. A single signature with the cloud as parameter is intentional — per-cloud tool names would invite the Generator to confuse which cloud's catalog it's checking against.
- A `web_search` tool for current syntax, API, and version lookups (Terraform provider syntax, current cloud API parameters, package versions). Distinct from `external_data_sources` registry calls — search is for syntax/freshness, registry is for authoritative metadata.
- No external_data_sources tools — all agent-discretion external lookups happened earlier in the pipeline.

**Provenance discipline.** The Generator does not produce manifest fields; it produces code. Code does not carry provenance metadata, but the Generator's reasoning trace is preserved in a `.generator-trace.per-phase-<id>.json` artifact for audit. The trace links each generated function to the manifest step it implements and the manifest field it populates (e.g., the `implementation.path`).

**Quality bar.**

- Generated code passes static checks (ruff, mypy at lab-conventional strictness).
- Inline detection emissions match the step's declared detections in the manifest.
- Function names match `step.function_name` declarations.
- Imports are minimal: only the cloud SDK appropriate to the phase's `runtime:*` facet (boto3 for AWS, azure-mgmt-* / azure-identity for Azure, google-cloud-* for GCP, PyGithub / requests for GitHub, etc.) plus manifest-declared dependencies. No over-engineering, no presentation libraries unless the manifest declares them. (Specific library *choices* per runtime are in the prompt overlay; this quality bar names the property — "only what the runtime requires plus what the manifest declares" — not specific library names.)
- All values consumed from AttackConfig are typed correctly per the manifest's input declarations.
- Cloud API calls validated against the per-cloud catalog (AWS IAM, Azure RBAC, GCP IAM permissions).
- **`produces_world_state` entries are populated with correct `identifier_kind`** (per `schema.md §4.5`). When the phase generates runtime-random identifiers (suffixed branch names, timestamped IAM users, UUID-suffixed buckets), the entry must use `identifier_kind: runtime_generated` with an `identifier_source` pointing into the phase's `run_phase()` return dict (the key the phase actually writes to). When identifiers are deterministic across runs, the entry uses `identifier_kind: static` with the literal value. Getting this wrong produces cleanup code that looks correct at validation but fails at runtime; the semantic cross-check verifies that runtime_generated entries' `identifier_source` paths resolve to declared phase outputs.
- Planted credentials use only patterns from the canonical lab-credentials catalog (so detection scenarios work and the safety-scan whitelist applies — see `validation.md §6.8`).

(Style preferences like try/except wrapping conventions live in the prompt overlay, not the architecture's quality bar.)

**Failure modes.**

- Generator produces code that doesn't implement a declared step → Validator catches via the semantic cross-check (every `step.function_name` must exist in the emitted module).
- Generator hallucinates a cloud API → Validator catches via the per-cloud catalog cross-check.
- Generator produces a step that calls APIs not declared in the manifest's input/output types → Validator flags as a manifest-implementation mismatch.
- Generator produces 200 lines for what should be 30 → Critic flags as over-engineering.
- Per-phase Generator finds itself needing a value type not in the manifest → fails the stage with `missing_value_type` error; the refinement coordinator routes back to the Planner (or further upstream to the Extractor if the type wasn't in the AttackSpec). Per-phase Generators have read-only access to value types; they cannot propose new ones.
- Per-phase Generator hits a capability boundary preventing implementation of a step at its declared reproducibility tier → fails the stage with structured feedback. The Generator does **not** re-classify the step; per `architecture.md §0.7`, classification authority is the Extractor's, the Planner carries it forward, and the Generator implements or routes back. The refinement loop routes back to the Planner (and possibly to the Extractor if it's an AttackSpec-level issue).

**Notes.**

- Per-phase Generators run **in parallel** when the framework decides phases are independent. Independence is defined operationally in `schema.md §4.5` and `pipeline.md §3.2.9`: phases share no `bind_inputs` chain AND no overlapping `produces_world_state` items. For phases with cross-phase data flow or shared state, the framework serializes them, but does not require global serialization.
- The Generator's input does not include other phases' implementations. Each phase is generated in isolation against its declared interface. This forces clean interfaces but means the Generator cannot peek at sibling code.
- **Phase-level oscillation handling** (per `pipeline.md §3.2.12`): if downstream stages keep regenerating because an upstream phase's outputs change, the refinement coordinator detects the cascade and routes the next iteration to the upstream phase rather than the downstream stages.

### 5.10 Per-phase Generator: code shapes and execution contexts

The per-phase Generator is **not** a fixed family of templates. Per `architecture.md §0.7` (lab class is emergent) and `schema.md §4.20` (preference ordering), the Generator's output is shaped by the manifest's per-phase declarations, not by an upfront template selection.

What the Generator's prompt provides: **canonical code-shape examples** for the major `step_composition` × `execution_context` combinations. These are illustrative reference patterns the agent uses as guidance, not fill-in-the-blank templates.

The dimensions that affect shape:

- `step_composition`: `sequential` vs `independent`.
- `execution_context`: `attacker_local`, `victim_vm_via_ssh`, `victim_lambda`, `victim_build_container`, `victim_serverless`, `victim_pod`, `github_actions_runner`, `other` (registry-backed; new contexts can be proposed by the Planner at runtime per `schema.md §4.16`).
- Number of platforms involved (single vs multi).
- **Credential mechanism** (illustrative values — `planted`, `generated`, `discovered_via_imds`, `discovered_via_metadata`, etc. — not a closed enum; the agent reasons about how credentials are obtained in the specific attack and adapts).
- Provisioning mechanism (Terraform vs CloudFormation vs ARM template vs CLI scripts vs mixed).

**Canonical examples included in the prompt** cover the most common combinations observed in the curated blog set:

- Sequential / attacker_local / single-cloud / planted-or-discovered (e.g., a typical AWS exploit script).
- Sequential / victim_vm_via_ssh / single-cloud / discovered_via_imds (SSH-into-VM variant).
- Independent / victim_vm_via_ssh / multi-cloud / discovered_via_imds_or_metadata (fan-out across compromised hosts).
- Sequential / attacker_local / single-cloud / build-container-trigger (CI/CD style).
- Sequential / mixed / multi-platform / generated-and-discovered (generic fallback for novel combinations).

The Generator picks the closest canonical example, adapts it to the phase's specifics, and fills in step bodies. New combinations not covered by canonical examples are handled by the agent generalizing — the prompt does not restrict to fixed templates.

**Adding new canonical examples** to the prompt happens as the eval harness shows the Generator struggling on combinations the current examples don't cover. This is implementation-level evolution, not architectural — the prompt examples evolve alongside the curated blog set.

**Why this beats a fixed template family**: locking the Generator to a fixed set of templates would force shoehorning of blogs that don't fit. Real cloud-relevant attacks vary too much. The agent + canonical examples + manifest declarations approach absorbs variation without requiring template proliferation. Consistent with `architecture.md §0.7`'s emergent-lab-class principle.

### 5.11 Lab-level Generator

**Job.** Generate the lab-level orchestration — `setup.sh`, the lab-level IaC (`infra/main.tf` by default; CloudFormation/ARM/etc. when phase declarations require), and the entry-point script (`attack/main.py`). The `verify.sh` and lab-level `cleanup.sh` are owned by the Cleanup Generator (§5.12) because they share world-state-traversal logic.

**Inputs.**

- The full manifest.
- All per-phase Generator outputs (so the lab-level orchestrator knows what to call).
- The user's user-config (for default values).

**Output.** Lab-level scripts and IaC.

**Tools.** Same as per-phase Generator, including `web_search` for current Terraform/IaC syntax. Read access to all phase implementations.

**Provenance discipline.** Same as per-phase Generator: code does not carry provenance, but a trace is emitted (`.generator-trace.lab-level.json`).

**Quality bar.**

- `setup.sh` follows an idempotent state-convergence pattern (pre-flight checks → local mutations → cloud/platform mutations) with re-run safety. The full style guide is loaded as part of the Lab-level Generator's prompt overlay, per `pipeline.md §3.5`'s prompt overlay model.
- `setup.sh` supports `--from-phase <id>` and resolves prerequisite phases automatically (per the chaptered-long-lab model from `architecture.md §0.6`).
- **Cleanup-confidence mechanical gate emitted.** When the Critic's per-phase confidence for cleanup-relevant phases is below the threshold from `architecture.md §0.5` criterion 2, `setup.sh` emits a check at startup that refuses to run without `--accept-low-cleanup-confidence` flag. The check reads the per-phase confidence values from the lab's `validation_report.json` so the gate's behavior is data-driven (the user can re-run validation after addressing concerns, and the gate updates accordingly). The framework determines whether to wire in this gate based on the Critic's verdict; the Lab-level Generator emits the gate code when instructed.
- Auto-fix prereqs (per `prereqs.pre_lab` with `kind: auto_fixable`) are wired correctly.
- `mid_lab` prereqs are surfaced at the right execution point (the orchestrator presents the consent prompt before the relevant phase runs).
- `attack/main.py` provides `--auto` and `--interactive` modes for lab execution.
- Lab-level IaC translates the manifest's `lab_resources` declarations into actual provisioned resources, exporting them as outputs that phases reference via `references_lab_outputs`.
- Per-phase IaC references lab-level state via `data "terraform_remote_state"` (or equivalent for non-Terraform mechanisms).

**Failure modes.**

- Setup script doesn't handle a declared auto-fixable prereq → Validator catches via cross-check.
- IaC mismatch between provisioning_mechanism declarations and what's actually emitted → Validator catches.
- Lab-level output names don't match what per-phase Generators reference → the Validator's semantic cross-check catches the contract violation.

**Notes.**

- The Lab-level Generator runs after all per-phase Generators complete. It cannot run in parallel with them because it needs to know what phases produced.
- Style guides (cleanup conventions, color coding, banner format) are part of the prompt context, not registry entries. The principles are: idempotent state-convergence, best-effort error accumulation, color-coded step numbering, mirror-image structure between setup.sh and cleanup.sh. Specific syntax lives in the prompts.

### 5.12 Cleanup Generator

**Job.** Generate the lab-level `cleanup.sh` that orchestrates per-phase cleanups (each written by its phase Generator) in correct reverse-DAG order, then handles cross-phase shared state and lab-level resource teardown. Also generates `verify.sh`.

**Three-tier cleanup architecture.** The architecture splits cleanup responsibility across three levels:

1. **Inline cleanup in phase code** — `try/finally` blocks in attack.py for resources the phase opens and closes within its own execution (open sessions, temporary handles, subprocess cleanups). Owned by the phase Generator as part of writing the attack code.

2. **Per-phase `cleanup.sh`** — written by each per-phase Generator (§5.9). Targets state the phase creates that persists beyond the phase's runtime (IAM users created via CLI, files dropped, ephemeral resources spun up imperatively). The phase's own author has the freshest context for its specifics.

3. **Lab-level `cleanup.sh`** — this agent's output. Orchestrates the per-phase cleanups, handles cross-phase shared state, runs `terraform destroy` for lab-level resources.

The Cleanup Generator's job is the third level. It does not duplicate the phases' work; it composes their work into a coherent whole.

**Inputs.**

- The full manifest (for `produces_world_state` across phases, `lab_resources`, phase DAG order).
- All phase implementations including their per-phase cleanup scripts (to verify they exist, understand what they cover, and detect gaps).
- The lab-level IaC (to know what `terraform destroy` or equivalent will handle).
- The cleanup style guide (loaded as prompt context).

**Output.** Top-level `cleanup.sh` plus `verify.sh` (the latter co-located here because it shares the world-state declaration as its check-list source — the Cleanup Generator owns world-state-traversal logic).

The lab-level `cleanup.sh` structure:

```
#!/bin/bash
# 1. Per-phase cleanups in reverse-DAG order
./attack/phase_5_persistence/cleanup.sh
./attack/phase_4_exfiltration/cleanup.sh
...
./attack/phase_1_initial_access/cleanup.sh

# 2. Cross-phase shared state (state no single phase owns)
# Examples: env vars set across multiple phases, shared temp directories
[scripted cleanup of cross-phase items]

# 3. Lab-level resources
cd infra && terraform destroy -auto-approve

# 4. Verification
./verify.sh

# 5. Manual checklist for items the system cannot verify automatically
[checklist printed]
```

**Tools.** Read access to the manifest and code; `web_search` for current Terraform destroy syntax / cloud CLI cleanup commands; cloud IAM catalogs for hallucination prevention; read access to the AttackSpec for cleanup ordering decisions that depend on attack semantics.

**Provenance discipline.** None for code; the orchestrator's reasoning trace records why each per-phase cleanup is invoked in its declared order and what cross-phase state items were identified (`.generator-trace.cleanup.json`).

**Quality bar.**

- Cleanup is idempotent (running twice is safe) and best-effort (errors during cleanup are logged but don't abort the remaining cleanup steps — a partially-cleaned environment is better than a stuck cleanup).
- Per-phase cleanups invoked in correct reverse-DAG order (derived from the manifest's phase `depends_on` graph).
- Cross-phase shared state correctly identified (state declared in multiple phases' `produces_world_state` blocks with the same identifier scope, or state declared at lab level that phases mutate).
- **`identifier_kind` correctly consumed** (per `schema.md §4.5`). For `produces_world_state` entries with `identifier_kind: static`, cleanup uses the literal identifier directly. For entries with `identifier_kind: runtime_generated`, cleanup reads from the `identifier_source` path at runtime — typically by sourcing the phase's output state file or reading from a known shared location populated by `run_phase()`. Generated cleanup code must NOT hardcode the placeholder identifier from the manifest for runtime_generated entries; doing so produces cleanup that looks correct but fails at runtime against the real resource.
- Lab-level Terraform destroy (or equivalent for other declared mechanisms) included.
- Mirror-image structure to setup.sh (same color coding, same step counter, same banner conventions).
- Manual verification checklist printed at end for items the system cannot verify automatically.
- `verify.sh` confirms cleanup succeeded; it does not check the lab was set up correctly. It runs after `cleanup.sh` in the lab-level orchestrator's flow.

**Failure modes.**

- Phase declared a world-state change but neither the per-phase cleanup nor lab-level cleanup addresses it → Validator catches via the semantic cross-check.
- Per-phase cleanup script doesn't exist when manifest declares the phase produces world state → Validator flags.
- Cleanup invokes per-phase scripts in wrong order → caught at Critic review (semantic correctness against attack DAG).
- Cleanup script uses `set -e` and aborts on first error → Critic flags.

**Notes.**

- The Cleanup Generator does not write cleanup *for* phases — it orchestrates the cleanups *written by* the phases. This is the architectural shift from the earlier single-cleanup model.
- The cleanup style guide is a prompt input. Principles: best-effort, idempotent, mirror-image with setup, manual checklist at end. Syntax lives in the prompt.
- `verify.sh` derives its check list from the union of all phases' `produces_world_state` declarations plus lab-level `lab_resources`. Same agent owns both `cleanup.sh` and `verify.sh` because both walk the same world-state declarations.

### 5.13 Docs Generator

**Job.** Generate the lab's documentation — root README (with per-phase confidence summary and `cyberlab-gen fix` pointer), attack guide (chaptered for long labs), concepts doc, attack narrative, MITRE mapping table, CNAPP detection mapping table, real-world examples doc, defender techniques (when present in AttackSpec).

**Inputs.**

- The full manifest with provenance.
- The AttackSpec (for blog-grounded content like real-world examples and defender techniques).
- The phase implementations (for code references).
- The Critic's QualityReport (for per-phase confidence values — see below).
- The doc structure templates (the strict 4-part concept template, the 6-column CNAPP table template, etc., loaded as prompt context).

**Output.** Root-level `README.md` plus a `docs/` directory:

- `README.md` (root) — entry point. Opens with a **"How to use this lab" section** that includes:
  - **Per-phase confidence summary**, derived from the Critic's per-phase confidence assessments via the validation report. The presentation uses three tiers consistent across labs (the exact threshold numbers are calibrated per `architecture.md §8.4`, but the tier structure is locked):
    - Phase with confidence ≥ 0.6: standard surfacing — phase listed with its confidence value.
    - Phase with confidence < 0.6 and ≥ 0.4: surfaced with explicit "low confidence — recommend reviewing this phase before running" framing.
    - Phase with confidence < 0.4: surfaced with explicit "low confidence — recommend regenerating" framing.
    - Cleanup-relevant phases with confidence below the cleanup-confidence gate threshold (per `architecture.md §0.5` criterion 2): the README explains that `setup.sh` will refuse to run without `--accept-low-cleanup-confidence` and what that means.
  - **`cyberlab-gen fix` pointer**: "If something doesn't work, run `cyberlab-gen fix .` from this directory and describe the problem."
  - For labs targeting non-first-class runtimes (per `schema.md §4.13`), an explicit note: "This lab targets `runtime:X`, which is not a first-class v1 runtime — expect more rough edges; please report issues."
  - Followed by overview, prerequisites summary, run instructions.
- `docs/attack_guide.md` — chaptered for long labs (each chapter corresponds to a natural chain break).
- `docs/concepts.md` — uses the 4-part template; substantive when AttackSpec has `vulnerability_story`.
- `docs/attack_narrative.md` — readable prose walkthrough. For AttackSpecs with non-empty `alternative_paths`, surfaces them as "the blog also describes these alternative paths, not generated in this lab."
- `docs/real_world_examples.md` — populated from `real_world_incidents`; "no incidents observed" status surfaces here when applicable.
- `docs/prerequisites.md`.
- `docs/defender_techniques.md` — present only when AttackSpec's `defender_techniques` block is non-empty (i.e., for incident-analysis blogs).
- `detection/mitre_mapping.md`.
- `detection/cnapp_mapping.md` — uses the 6-column table template.

**Tools.** Read access to manifest, AttackSpec, code, validation report; `web_search` for current security tooling references, current MITRE technique names, current CNAPP vendor names (lower stakes than code generators but still useful for keeping user-facing docs from being stale).

**Provenance discipline.** Docs surface provenance: every claim sourced from the blog has a citation; every external API value has its source noted; every LLM inference has the reasoning shown (typically as a footnote or detail block). Discrepancy cases (`external_api` source with both blog and API citations) are surfaced honestly (e.g., "the blog stated severity high but per NVD this is medium"). Trace emitted as `.generator-trace.docs.json`.

**Quality bar.**

- All standard doc sections present.
- Doc structure matches templates exactly (concepts use the 4-part shape, CNAPP mapping uses the 6-column table).
- Cross-references between docs are consistent (the attack_guide's step numbering matches the manifest's step ids).
- Citations are present for blog-derived claims.
- **Every substantive technical claim is grounded.** Each claim about how the attack works, what the vulnerability is, what the cloud APIs do, or what the detection logic does must trace to the AttackSpec, the validation report, or a `web_search` result with citation. No LLM-original technical claims — if a fact isn't in one of the grounded sources, it doesn't appear in the docs. This is the same search-before-claim discipline imposed on the Extractor (§5.4) and Critic (§5.14), applied to the agent that writes user-facing content.
- No fabricated real-world incidents; only those in the AttackSpec's `real_world_incidents` block.
- Defenses surfaced with appropriate framing per their `applicability` (customer_actionable as actionable recommendation; vendor_only as factual context; etc.).
- For demonstration-only labs, docs honestly describe what the user will see (no overclaiming reproduction).
- Long labs produce chaptered attack guide; the `setup.sh --from-phase` mechanism is documented.
- README's "How to use this lab" section is present and includes the per-phase confidence summary and `fix` pointer.

**Failure modes.**

- Docs reference a step or phase that doesn't exist → Validator catches.
- Docs invent citations not in the AttackSpec → Critic flags.
- Doc structure deviates from templates → Critic flags as inconsistency.

**Notes.**

- Docs are templated, not free-form. The Docs Generator's job is filling in templates with manifest content, not deciding doc structure.
- The CNAPP mapping table is fully derivable from per-step detection declarations. The Docs Generator emits the table by walking phases × steps × detections.

### 5.14 Critic

**Job.** Holistic quality assessment of the complete generated lab. Score the lab on a rubric, score per-phase confidence, flag concerns. Recommend refinement or approval.

**Inputs.**

- The complete lab directory (manifest, AttackSpec, all generated artifacts).
- The AttackSpec (explicit input — needed for fidelity-against-spec checks).
- The validation report from the Validator (so the Critic can comment on whether mechanical findings are real concerns or noise, without re-checking).
- The original blog content.
- Blog excerpts mapped to phases.

**Output.** A structured `QualityReport` with:

- **Per-dimension rubric scores** (whole-lab):
  - Fidelity to blog.
  - Completeness.
  - **Implementation correctness against attack semantics** (not semantic-cross-check territory — see "Correctness narrowed" below).
  - Code quality.
  - Doc quality.
  - Cleanup quality.
- **Per-phase confidence scores**, with concerns per phase. These feed the README's "How to use this lab" section (per §5.13) and the per-phase entries in `validation-report.md`. Enable the user to know which phases are uncertain at runtime — supporting the always-ship-with-honest-confidence model from `architecture.md §0.5 criterion 2`.
- **Specific concerns flagged** (whole-lab and per-phase).
- **Refinement recommendations** (which agent to re-run, what to focus on).
- **Overall verdict**: `approve` / `refine` / `reject`.

**Tools.**

- Read access to all generated artifacts and the original blog.
- `web_search` for verifying current syntax/API/version (e.g., is this Terraform AWS provider syntax current?). **Framework-tracked per-run cap** (v1 placeholder: 5 calls per Critic run, pending eval-harness calibration per `architecture.md §8.4`). Exceeding the cap fails the stage rather than relying on prompt-level "sparingly" discipline — consistent with how the Extractor's external API budget is enforced.
- No external_data_sources tools — the Validator already ran them, and the juries verified provenance.

**Provenance discipline.** Critic feedback itself is LLM-inference-with-reasoning. Every concern has a citation back to the artifact + the rationale.

**Correctness narrowed (avoiding double-count with the semantic cross-check).** The Validator's semantic cross-check already checks "does the implementation match the manifest's declarations" (function names match, declared outputs returned, world-state items in cleanup). The Critic does **not** re-verify these mechanical checks. The Critic's "implementation correctness against attack semantics" assesses:

- Whether declared types and shapes are *correct for the attack* (not just internally consistent — e.g., manifest declares `aws_credentials` and code returns `aws_credentials`, but they're IAM-user creds when the attack semantically requires role-assumption creds).
- Whether the attack's semantics are reproduced (e.g., the IAM policy actually grants the access the attack needs; the payload actually exploits the vulnerability).
- Whether fallback decisions made by the Generator (per `schema.md §4.20`) were honest — did the agent settle for `partial_simulation` when `full` was achievable? At implementation level — LabManifest-level fallbacks were reviewed by the Planner-Jury per §5.8.

**Non-first-class runtime adjustment.** When the lab targets non-first-class runtimes (per `schema.md §4.13`), the Critic's confidence reflects reduced coverage in semantic cross-checks and the absence of platform-specific verification.

**Quality bar.**

- The Critic is **advisory** (per `architecture.md §1.6` locked decision). Its verdict feeds the refinement loop's stopping strategy and the lab's per-phase confidence flags, but never directly blocks shipping a lab.
- After exhausted refinement, the lab ships with the Critic's concerns prominently surfaced in `validation-report.md` and per-phase confidence flags in README. The user decides whether to try the lab (and use `fix` mode if issues arise) or regenerate.
- The Critic should be calibrated against the eval harness: its scores should correlate with manual quality assessment on the curated set.

**Provenance verification division of labor.** The Critic does not re-verify external_api provenance; that verification is the Extractor-Jury's and Planner-Jury's responsibility. The Critic assesses whether the lab implements the approved AttackSpec and Manifest faithfully — semantic fidelity, not provenance correctness.

**Failure modes.**

- Critic produces consistently high scores regardless of lab quality → calibration issue, surfaced in eval harness.
- Critic flags concerns that don't correspond to actual issues → noise, surfaced over time via false-rejection rate tracking.

**Notes.**

- The Critic's role is similar to a jury (review and feedback) but distinct: juries are gate checks for specific stages; the Critic is a holistic final assessment that runs as a peer stage to the Validator.
- Multi-model Critic (when the user has multiple providers) is supported; agreement across models is a stronger quality signal.
- The Critic's prompt explicitly lists known anti-patterns (over-engineering, hallucinated APIs, citation fraud, missing detections, weak cleanup, dishonest fallback to lower fidelity) so it knows what to look for.

### 5.15 Refinement loop coordinator (framework, not agent)

Specified in `pipeline.md §3.2.12`; not repeated here. Brief: deterministic framework code that consumes the Validator's report and the Critic's verdict, decides whether to re-run a stage and which one, distinguishes cycle/repeat/cascade oscillation patterns, retains best-state snapshots, and bounds total spend per `architecture.md §1.7`.

### 5.16 Repair Agent

**Job.** In a conversational REPL session (the fix pipeline, `pipeline.md §3.4`), help the user fix runtime issues encountered when running a generated lab. Read the lab and its provenance, reason about user-reported problems, propose minimal patches or environmental-issue explanations, engage in back-and-forth until issues resolve or the user exits.

**Inputs (at session startup).**

- `lab.yaml` (manifest).
- `.cyberlab-gen/generation_report.json` (summary; full content available via tool).
- `.cyberlab-gen/validation_report.json` (summary, including per-phase confidence).
- `.cyberlab-gen/fix_history.json` if prior sessions exist (for cross-session continuity).
- The blog excerpts referenced by the manifest (small per-step).

**Inputs (during session).**

- Live conversation history (maintained in memory by the framework for the session's duration; each user message triggers a new stateless agent call with the prior conversation included as context).
- User messages (problem reports, paste-ins of error output, follow-up clarifications).

**Output.** One of the following per turn:

- A `propose_patch(file_path, diff)` for a code change.
- A `propose_doc_update(file_path, diff)` for a doc clarification.
- An `explain_environmental_issue(diagnosis, user_action)` structured explanation when the problem is in the user's environment, not the lab.
- A `request_more_info(question)` follow-up question (one focused question at a time, not a list).

**Tools.**

- `read_lab_file(relative_path)` — lazy-load files from the lab directory on demand.
- `list_lab_files(subdir)` — list files in a subdirectory.
- `read_provenance(field)` — query specific provenance entries from the generation report.
- `web_search` — for current syntax, API, version lookups. Essential for fixing stale-syntax issues (e.g., the user pastes an error about a Terraform provider that was updated; the agent verifies current syntax).
- The four output tools (`propose_patch`, `propose_doc_update`, `explain_environmental_issue`, `request_more_info`).
- **No write access.** Every patch goes through user review and framework-applied write.
- **No lab execution.** The agent never runs the lab; only the user does.
- **No cloud API access.** The agent reasons about cloud problems from user-pasted output, not by querying the cloud.
- A heuristic credential-paste detector: when the user's paste contains patterns matching real-credential heuristics (and not the canonical lab-credentials catalog), the agent surfaces a warning: "This paste appears to contain credential fragments; redact before continuing." Cheap, helpful, prevents user-introduced leaks via `fix_history.json`.

**Provenance discipline.** The Repair Agent does not produce manifest fields; it produces patches. Patches accumulate in `.cyberlab-gen/fix_history.json` along with the agent's reasoning and the user's accept/reject decisions, providing an audit trail for the lab's evolution after generation.

**Quality bar.**

- Patches are minimal and targeted. **Mechanical thresholds** (v1 placeholders pending eval-harness data per `architecture.md §8.4`): a patch modifies no more than 3 files per turn; a patch touching the manifest requires an explicit justification field populated by the agent. A patch exceeding the file count or touching the manifest without justification halts the turn with structured feedback asking the user whether to proceed (legitimate cases exist — adding a missing import across phases, for instance — but they're rare enough to warrant explicit user acknowledgment).
- Each patch passes the minimal validation flow before being shown to the user: static-schema validation if manifest touched, the semantic cross-check for cross-checks, safety scans. The containerized dry-run auto-runs when the patch touches IaC files; `--validate-patches-thoroughly` controls the other cases. Real-platform apply not applicable (its slot stays v2-deferred).
- The agent honestly distinguishes "this is a lab bug I can fix" from "this is your environment, here's what you need to do."
- Web search is used for syntax/freshness verification, not as a primary information source.
- Follow-up questions are focused — one specific question at a time, not a list.

**Failure modes.**

- Agent proposes a patch that fails minimal validation → validation feedback surfaced to agent for revision; user sees both the revised patch and the validation finding that drove the revision.
- Agent can't determine the problem from the user's information → `request_more_info` with a specific question.
- User and agent disagree on the diagnosis → conversation continues; the user can override by editing files themselves outside the REPL (and continuing the conversation).
- Budget exhausted mid-session → budget-overrun interrupt (per `pipeline.md §3.1.1`) — user can raise cap, exit, or proceed past cap explicitly.

**Notes.**

- The Repair Agent is stateless per call (like every other agent). Conversation continuity within a session comes from the framework maintaining live conversation history; cross-session continuity comes from `fix_history.json`.
- The agent has the full lab context available via lazy-load tools, but only loads what the conversation indicates is relevant. For small labs, the framework may preload all files as an optimization (transparent to the agent).
- The agent never branches back to upstream agents (Extractor, Planner, generation Generators). If the user concludes a phase is fundamentally wrong, the agent directs them to `cyberlab-gen generate` for that scenario; it doesn't regenerate from within `fix`. `--regenerate-phase` is v1.5+ deferred.
- Cross-lab learning is not automated — successful fix patterns stay in this lab's `fix_history.json`. Maintainer review can promote patterns to registry `notes_for_generator` updates or prompt overlays (manual process; automation is v1.5+ deferred per `architecture.md §8.2`).

### 5.17 Inter-agent communication

Agents do not call each other directly. The framework reads each agent's output, validates it against the relevant schema, and feeds it as input to the next stage. This:

- Enforces the contract (every output is validated).
- Allows the framework to inspect, log, persist, and (in interactive mode) display intermediate artifacts.
- Lets the refinement loop re-run a stage with isolated state.

The communication artifacts table is in `pipeline.md §3.3`.

Every artifact is persisted to disk in the lab's working directory. The user can inspect any artifact at any time (interactive mode pauses the pipeline; auto mode runs through but artifacts remain on disk).

### 5.18 Tool inventory across agents

A consolidated view of which agents have which tools. Split into two tables: generation pipeline (9 agents) and fix pipeline (1 agent), because the Repair Agent's tools are largely orthogonal to the generation agents'.

#### Generation pipeline agents

| Tool | Extractor | Extr-Jury | Planner | Plan-Jury | Per-phase Gen | Lab Gen | Cleanup Gen | Docs Gen | Critic |
|---|---|---|---|---|---|---|---|---|---|
| External data sources | yes | yes | yes | yes | no | no | no | no | no |
| Propose value_type | yes | flag | no | flag | no | no | no | no | no |
| Propose facet (target, blog-derived lab_class_signal) | yes | flag | flag | flag | no | no | no | no | no |
| Propose facet (runtime, lab-derived lab_class_signal) | no | — | yes | flag | no | no | no | no | no |
| `lookup_cloud_iam_action(cloud, action)` — static catalogs (AWS/Azure/GCP) | no | no | no | no | yes | yes | yes | no | yes |
| `web_search` | no | no | no | no | yes | yes | yes | yes | yes (capped per-run) |
| Canonical lab-credentials catalog | no | no | no | no | yes (read) | yes (read) | yes (read) | no | yes (read) |
| Read manifest | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Read AttackSpec | yes | yes | yes | yes | partial | yes | yes | yes | yes |
| Read blog content | yes | yes | yes | yes | partial | no | no | yes | yes |
| Read other agents' artifacts | no | no | no | no | no | yes | yes | yes | yes |
| Read Validator report | no | no | no | no | no | no | no | no | yes |

`partial` means the agent sees only the relevant slice (its phase's excerpts, not the full blog).

`flag` means the jury can flag missing or wrong proposals from the upstream agent but cannot make new proposals itself. Reserved for the two juries. For target/blog-derived lab_class_signal facets, both the Extractor (as proposer) and the Planner (as a downstream consumer) can flag if they observe gaps — this is one of the rare cases where the Planner has a flag role.

The Critic's `no` on external data sources, propose value_type, and propose facet is intentional. Provenance verification belongs to the juries; registry-proposal authorship belongs to the Extractor (and Planner for runtime/lab-derived facets). The Critic has access to `web_search` for syntax/freshness spot-checks but does not access the structured external_data_sources registry (those calls already happened upstream and were verified by the juries). If the Critic observes that an `extras` block was used for content that probably should have been a registry entry, it surfaces the observation in its structured concerns list as a quality issue (informing future Extractor improvements), not as a new registry proposal.

#### Fix pipeline (Repair Agent)

| Tool | Repair Agent |
|---|---|
| `read_lab_file`, `list_lab_files`, `read_provenance` | yes |
| `web_search` | yes |
| `propose_patch`, `propose_doc_update`, `explain_environmental_issue`, `request_more_info` | yes |
| Credential-paste detector heuristic | yes (warns user) |
| External data sources registry | no |
| Cloud IAM catalogs | no |
| Write access to files | no (framework applies after user approval) |
| Lab execution | no |
| Cloud API access | no |

### 5.19 Cost and budget per stage

Each agent stage has a token budget recorded in the configuration. Total per-lab cost is bounded by the `architecture.md §1.7` caps.

**Placeholder per-agent token budgets** (v1, pending eval-harness measurement per `architecture.md §8.4`):

| Agent | Input tokens | Output tokens |
|---|---|---|
| Extractor | 50K | 20K |
| Extractor-Jury | 30K | 10K |
| Planner | 40K | 15K |
| Planner-Jury | 25K | 8K |
| Per-phase Generator (per phase) | 30K | 20K |
| Lab-level Generator | 40K | 25K |
| Cleanup Generator | 30K | 15K |
| Docs Generator | 50K | 40K |
| Critic | 60K | 15K |
| Repair Agent (per fix session, default cap $5) | varies | varies |

**Important: these placeholders are known to be over-aggregated for the $10 cap.** Summing the per-agent figures across all stages plus jury invocations exceeds $10 at current frontier-model pricing. Either the cap is too tight, the per-agent budgets are too generous, or both. The first eval-harness run measures actual usage on the curated set and produces calibrated values for v1 release. Until then, expect cost-cap warnings on most runs; users can override the cap or wait for calibrated defaults.

**Per-phase Generator: 30K input / 20K output is per phase.** A 5-phase lab incurs 5× this. Plus refinement loop iterations (multiplier on the above, capped by the iteration / spend cap).

**Token-to-dollar reconciliation.** The framework converts token counts to spend using the provider's published pricing for the active model. The `--max-llm-cost` cap is in dollars; per-agent budgets are in tokens; the framework reconciles.

Per-model cost tracking (per `pipeline.md §3.5`) makes these comparable across providers. The eval harness measures cost per lab; the cost-per-quality ratio is one of its metrics and a primary input to retuning these budgets (`eval.md §7.4`).

### 5.20 Agent boundary discipline

Agents have overlapping concerns by nature — the Planner thinks about reproducibility, the Critic thinks about reproducibility, the Planner-Jury thinks about reproducibility. The risk is that overlap becomes ambiguity: who *actually* checks what? The architecture resolves this with three rules and an ownership table.

**Rule 1: Each concern has a primary owner.** For any quality dimension, exactly one agent (or one Validator layer) is responsible for it. Other agents may inform or feed back, but the owner is named. "Both X and Y check this" is not a boundary; one of them is the owner and the other is the informant.

**Rule 2: Earlier stages own structural decisions; later stages own implementation correctness.** The Planner owns "is this lab well-structured?" The Critic owns "is the implementation correct against the structure?" The Planner cannot check implementation (no code exists yet). The Critic cannot restructure (refinement loop's job, routing back to Planner if needed).

**Rule 3: Juries verify upstream, never propose.** A jury's role is reviewing what the upstream agent produced. If a jury thinks something is missing, it flags the upstream agent to add it; the jury does not add it itself. This preserves the jury as a check, not a competing producer.

#### Ownership table

| Concern | Primary owner | Other agents involved |
|---|---|---|
| Blog fidelity (extraction faithfulness) | Extractor-Jury | Critic (informs as feedback) |
| AttackSpec structural validity | Validator static-schema validation | Extractor-Jury (semantic check) |
| Provenance correctness on AttackSpec fields | Extractor-Jury | — |
| Phase decomposition reasonableness | Planner-Jury | — |
| LabManifest structural validity | Validator static-schema validation | Planner-Jury (semantic check) |
| Provenance correctness on Manifest fields | Planner-Jury | — |
| Fallback decision honesty (LabManifest-level — what the Planner chose) | Planner-Jury | — |
| Fallback decision honesty (implementation-level — what the Generator chose) | Critic | — |
| Implementation correctness against attack semantics | Critic | Validator semantic cross-check (mechanical sub-check) |
| Code-manifest cross-check (mechanical) | Validator semantic cross-check | — |
| Static analysis (linters, typecheckers, IaC scanners) | Validator containerized dry-run | — |
| Safety scans (credentials, host attacks) | Validator safety scans | — |
| Code quality, doc quality, cleanup quality | Critic | — |
| Per-phase confidence assessment | Critic | (surfaces in README and validation-report.md) |
| Registry proposal authorship (value types) | Extractor | Extractor-Jury reviews |
| Registry proposal authorship (target / blog-derived lab_class_signal facets) | Extractor | Extractor-Jury reviews; Planner may flag downstream gaps |
| Registry proposal authorship (runtime / lab-derived lab_class_signal facets) | Planner | Planner-Jury reviews |
| Registry proposal acceptance | User (interactive: Accept/Edit) / auto-accept (auto, capped) | — |
| Runtime issue diagnosis (user-reported, post-generation) | Repair Agent (in fix session) | — |
| Patch correctness against runtime errors | Repair Agent | Minimal validation (static-schema, semantic cross-check, safety scans) |
| Patch application | Framework (after user review) | — |

**Per-proposal "no reject" semantics.** Per `schema.md §4.16`, there is no "Reject" option on per-proposal menus. The per-proposal menu offers Accept and Edit. A user who disagrees with a proposal has three real paths: Edit, provide upstream-agent feedback at the artifact level, or Abort.

(The real-platform apply row is omitted — real-platform apply is v2-deferred per `architecture.md §8.1`; its slot stays reserved so v2 adds it back without renumbering. It will return to this ownership table when v2 ships.)

Reading the table:

- Each row has exactly one owner. "Other agents involved" means they may inform or feed back, but they are not authoritative for that concern.
- "Provenance correctness" is split — the Extractor-Jury checks AttackSpec provenance, the Planner-Jury checks Manifest provenance. The Critic does not re-verify provenance (different concerns at different stages).
- "Fallback decision honesty" is split between LabManifest-level (Planner-Jury) and implementation-level (Critic). Different artifacts, different observers, different levels.

This table is the canonical answer to "who checks X?" If a quality concern arises that doesn't have a row here, the architecture has a gap — add the row, choose the owner, document.

### 5.21 Section summary

The system has ten agents: nine in the generation pipeline (Extractor, Extractor-Jury, Planner, Planner-Jury, four Generators, Critic) and one in the fix pipeline (Repair Agent). Each agent has a clear contract: typed inputs, typed output, declared tools, defined quality bar.

The framework, not agents, runs control flow and validation. Agents reason about content; framework enforces structure. This split prevents the failure mode where an LLM decides to skip a check or override blog content with API content.

The provenance discipline established in `schema.md §4.9` flows through every agent: each output carries source/citation/confidence metadata, and juries verify that metadata against ground truth. The system is auditable end-to-end.

The per-phase Generator is shaped by manifest declarations and canonical code-shape examples, not by a fixed template family. New combinations are absorbed by the agent generalizing, not by adding templates. This lets lab class be emergent (per `architecture.md §0.7`) rather than pre-classified.

The Critic is advisory by design (per `architecture.md §1.6` locked decision) but feeds the refinement loop and produces per-phase confidence that surfaces in the README and validation report. Calibration thresholds and retry counts are tunable via eval-harness data, not fixed. A Critic `reject` after exhausted refinement does not block shipping; the lab ships with the rejection prominently surfaced.

Cleanup is a three-tier hybrid (per §5.9 and §5.12): inline `try/finally` in phase code for in-runtime resources; per-phase `cleanup.sh` written by the phase agent for state the phase persists; lab-level `cleanup.sh` written by the Cleanup Generator that orchestrates the per-phase scripts in reverse-DAG order plus handles cross-phase shared state and lab-level resource teardown.

Post-generation, the Repair Agent (§5.16) handles user-reported runtime issues conversationally — peer pipeline to generation, separate budget, separate state. Real-platform apply validation is deferred from v1 (its slot stays reserved for v2); v1 validates statically via the static-schema, semantic cross-check, containerized dry-run, and safety-scan passes, and labs are validated against real cloud by the user running them — with Repair Agent assistance when needed.

The agent boundary discipline (§5.20) names a primary owner for every concern. Overlap is informational, never authoritative.

**Cost honesty.** The per-agent token budgets in §5.19 are placeholders known to sum to more than the $10 cap. This is acknowledged, not hidden — see §5.19 for the explicit caveat and `architecture.md §8.4` for the pre-release calibration commitment that replaces these placeholders with informed defaults before v1 ships.

---

*End of agents document. See `pipeline.md` for how stages connect, `schema.md` for artifact shapes, `validation.md` for validator details, and `eval.md` for the eval harness.*
