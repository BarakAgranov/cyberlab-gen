# cyberlab-gen — System Architecture

**Status:** v1 draft. Living document thereafter.
**Document scope:** High-level architecture. Component contracts, system boundaries, locked decisions, deferred decisions. Implementation details (exact prompts, validator rule sets, code-level design) are not in this document.
**Architect:** Barak Agranov

This is the hub document. It covers foundations, foundational design decisions, the system map, deferred work, and document conventions. Other architectural areas live in companion documents in this directory:

- `pipeline.md` — the generation and fix pipelines, stage by stage.
- `schema.md` — manifest, AttackSpec, registries, provenance.
- `agents.md` — agent contracts (Extractor, Planner, Generators, Critic, juries, Repair Agent).
- `validation.md` — validator layers (Layer 1, 2, 3, 5; Layer 4 v2-deferred).
- `eval.md` — eval harness, mechanical and subjective metrics, telemetry feedback loop.

Cross-references in this document use section numbers within a file (`§1.5`) and file names for cross-file references (`pipeline.md §3.2`).

**How to read this architecture.** New readers can orient fastest by reading two cross-document sections before the rest: `agents.md §5.20` (the ownership table that names primary owners for every concern) and `schema.md §4.20` (the choice discipline that consolidates fallback ladders, categorical choices, and cumulative selections). These two sections are the densest concentrations of architectural commitments; everything else hangs off them. After those, the documents read in order: this hub, then `pipeline.md`, `agents.md`, `schema.md`, `validation.md`, `eval.md`.

---

## 0. Foundations

### 0.1 What this system is

`cyberlab-gen` is a command-line tool that takes a published security writeup (blog post, research report, advisory) and produces a runnable, validated, hands-on cyber lab — code, infrastructure-as-code, attack scripts, detection rules, lifecycle scripts, documentation, and a structured manifest describing what was generated.

The tool is open-source. A user clones the repo (or installs the package), provides their own LLM API keys, points the tool at a blog URL, and gets a lab directory they can run on their own machine and against their own cloud accounts.

The tool is single-user by design. It runs locally, generates locally, validates locally, writes output locally. There is no shared service, no shared state, no shared catalog, no operator. The user is the operator, the tenant, and the consumer.

The tool generates purple-team and educational labs against lab-controlled targets the user provisions. It is not a substitute for penetration testing, threat intel, or incident response, and it is not designed to produce operational offensive tooling.

### 0.2 Scope

**Cloud-relevant attack writeups.** v1 targets cloud-relevant attack categories: cloud misconfigurations, supply-chain attacks that traverse into cloud, CI/CD pipeline attacks, identity-and-access attacks (cloud and identity-tier including Entra ID hybrid surfaces), name-confusion attacks, and container/orchestrator attacks. The "cloud-relevant" framing covers adjacent surfaces (npm registries, container registries, identity tiers that span cloud and SaaS) when the attack chain terminates at cloud-side compromise.

**Runtimes in v1.** Four first-class runtimes ship with v1: AWS, Azure, GCP, and GitHub. First-class means: per-platform validator coverage, credential check conventions, and cleanup support are built in. Additional runtimes can be proposed at planning time and generate labs in a best-effort mode with reduced coverage flags; see `schema.md §4.13` for the full first-class-vs-best-effort model.

**Scope is enforced by agent judgment, not by registry coverage.** The registries are seeded with v1 attack categories, but they are not the gate. When the Extractor encounters typed values, facets, or external sources the bundled registry doesn't cover, it proposes new entries (see `schema.md §4.16`); proposals are reviewed and accepted, and the registry grows by addition. A blog about a brand-new attack on a brand-new technology can produce a lab on day one if the agents can reason coherently about its content.

Scope is enforced at two checkpoints:

- The **Extractor** marks a blog `out_of_scope` (with reason) when its content cannot be turned into a lab the system can meaningfully produce.
- The **Planner** refuses with `cannot_plan` when the AttackSpec implies infrastructure the system cannot express as code (rare; most boundary cases are handled by the per-step reproducibility mechanism below).

Separately, **per-step fidelity** is handled by the reproducibility ladder (see `schema.md §4.20`): `full` → `partial_simulation` → `demonstration_only` → `not_reproducible` (the step is dropped from the lab). The reproducibility ladder is a fidelity mechanism, not a scope mechanism — a lab can be in-scope and still have steps that drop to lower tiers.

**Categories explicitly deprioritized for v1.** Pure on-prem AD, endpoint malware, and on-prem network attacks that don't traverse into cloud or supply-chain surfaces are not seeded in the registries. The Extractor will produce an `out_of_scope` result for blogs centered on these categories. They are not hard-banned — agent reasoning could in principle propose what's needed — but the system isn't tuned for them, and users should not expect first-class results.

### 0.3 Audience

Two audiences, in order of priority:

1. **Security and detection engineers** who read attack writeups and want to actually run them — to understand the attack chain, write detections, validate existing detections, build training material, or learn investigation techniques from incident analyses.
2. **AI/security engineers studying the system itself** — people who want to learn how an agentic generation system is built, fork it, modify it, contribute back.

Both audiences are CLI-comfortable. Both can read YAML. Neither expects a polished GUI. The tool's UX target is "feels like a serious developer tool" — comparable in expected sophistication to `terraform`, `kubectl`, `gh`, `act`, `pulumi`. Not friendly to absolute beginners; not hostile to them either.

### 0.4 Architecture vs. implementation (the line)

This document covers *what* each component does and what its contract is. It does not cover *how* a component does its job.

- **In scope here:** component identity, inputs, outputs, contracts, dependencies, failure modes, security boundaries, locked decisions, deferred decisions.
- **Out of scope here:** agent prompts, exact validator rule sets, exact eval test cases, code-level design, CLI flag inventory, output directory layout details, setup/cleanup style guides. These live in implementation documents drilled down from this architecture.

The split keeps the architecture readable as a stable contract while implementation details evolve at their own pace below it.

### 0.5 Top-level success criteria

A `cyberlab-gen` v1 is successful if all of the following hold:

1. **Headline path works.** A user can `cyberlab-gen generate <url>` against a cloud-relevant blog and get a working lab in a single invocation, without manual intervention beyond approval prompts, with progressive cost estimates emitted before each major spending stage.

2. **Generated labs ship with honest confidence assessment.** Every generated lab carries per-phase confidence flags from the Critic. Labs that pass mechanical validation cleanly ship with high confidence. Labs whose refinement loop exhausted its budget ship with the best snapshot retained, prominent flags in the validation report, and per-phase confidence surfaced in the README. The user is told what's uncertain and pointed to `cyberlab-gen fix` (see `pipeline.md §3.4`) for runtime issues.

   **Cleanup-confidence mechanical gate.** When per-phase confidence for cleanup-relevant phases is below threshold (v1 placeholder 0.5; calibrated per §8.4), the generated `setup.sh` refuses to run without an explicit `--accept-low-cleanup-confidence` flag. This is the one case where a flagged-but-shipped lab can cause material harm to the user (orphaned cloud resources they pay for), so the friction is mechanical, not advisory — consistent with §1.6's "mechanizable safety-critical checks are mechanical, never LLM-based" principle.

   True abandonment — no lab shipped — is reserved for cases where no coherent artifact was produced (Extractor refused as out-of-scope, Planner refused as unplanable, or a structural failure before any phase was generated).

3. **Generated labs are runnable.** A user who follows the generated root-level `README.md` and runs the generated `setup.sh` can get the lab running on their own cloud account (or other declared platform) without surprise prerequisites or undocumented manual steps.

4. **Generated labs are tearable-down.** Every lab produces a `cleanup.sh` and a `verify.sh`. v1 verifies cleanup as a *static* property: the generated cleanup orchestration covers every declared `produces_world_state` item across phases plus the lab-level `lab_resources`, and the Validator confirms this coverage at Layer 2. End-to-end real-platform verification (actually running cleanup against a real account and confirming no orphaned resources remain) is v2-deferred along with Layer 4 (see §8). When runtime cleanup issues arise — hallucinated resource IDs, missing permissions, race conditions — `cyberlab-gen fix` is the v1 mechanism for addressing them with user-reviewed patches. The cleanup-confidence mechanical gate (criterion 2 above) is the v1 safety net for cases where the system itself is uncertain about cleanup correctness.

5. **The schema is the contract.** Every generated lab carries a structured manifest (`lab.yaml`) that fully describes what the lab is, what it depends on, what it produces, and what it cleans up. The manifest is human-readable and machine-validatable.

6. **The system is honest about cost.** Cost estimates are produced progressively: a coarse estimate before Extractor runs, a refined estimate after Extractor, a final estimate after Planner. The user can abort at each interrupt point if estimates exceed their tolerance. Budgets are tracked with configurable caps; when the next stage's estimated cost would exceed the cap, the system surfaces a budget-overrun interrupt in both interactive and auto modes (caps are the user's stated limit; silently exceeding them is worse than briefly breaking auto mode). The specific cost caps and per-agent budgets that drive these estimates are placeholders pending empirical data; see §8.4 for the full list of calibration items.

7. **The system is honest about failure.** When generation fails to produce a coherent artifact, the user gets a structured explanation of *what* failed, *why*, and *what they could try next* — not an unhelpful traceback or a too-cheerful "something went wrong."

8. **The system improves over time.** An eval harness measures generation quality on a curated blog set plus a held-out set (held-out integrity protected by rotation, per `eval.md §7` — best-effort with structural safeguards, not a cryptographic guarantee). New strategies are compared against existing ones with real data, not intuition. Regressions are caught before release.

These are the criteria the rest of the architecture is in service of.

### 0.6 Non-goals (explicit)

Things we are deliberately not building, with reasoning:

- **No hosted service.** A user cloning and running on their own machine is the only supported model. Hosted-anything adds operational, security, and product complexity that fights the goal of a focused, gettable, hackable tool.

- **No multi-user features.** No sharing, no collaboration, no team accounts, no published-lab catalog hosted by the project. Generated labs live on the user's disk; if they want to share, they push to GitHub themselves.

- **No background telemetry.** The tool does not phone home automatically. Per-run reports are written locally; users can submit them explicitly via a dedicated CLI subcommand. See `pipeline.md §3.6`.

- **No schema migration of existing labs, ever.** Once generated, a lab is a folder on the user's machine, decoupled from the tool. The user can run it, modify it, archive it, push it to GitHub, ignore it — it's theirs. The lab's `setup.sh`, `cleanup.sh`, and `verify.sh` run against the user's environment without invoking cyberlab-gen at runtime. The tool does not reach into existing lab directories to migrate them when the schema evolves. If a user wants a current-schema lab, they re-run `generate` against the original blog URL. The tool refuses to *load* old-schema artifacts (for example, on `cyberlab-gen validate <lab-dir>` against an old lab) with a clear "regenerate from blog URL" message rather than attempting any migration.

- **No "agent that runs the lab for you."** The tool generates; the user runs. The reasons are safety (the user must consciously execute against their cloud), pedagogical (the point is hands-on), architectural (running labs is a different problem from generating them), and threat-model alignment (an agent autonomously running these labs would do exactly what defenders are supposed to detect — an AI system silently executing attack chains in cloud accounts is the threat model, not its solution). The `cyberlab-gen fix` mode (see `pipeline.md §3.4`) helps the user *debug* a lab they ran, but never executes the lab itself.

- **No multi-lab generation from one blog.** One blog produces one lab. If the blog is long, the lab has many phases, organized via chaptered documentation. The system does not support splicing a single blog into multiple labs, and does not support generating partial labs from sections of a blog. (The `--from-phase` mechanism is a *resume* tool for failed generation runs, not a way to slice blogs.)

- **No autonomous real-cloud apply during generation in v1.** Layer 4 (real-platform apply validation) is deferred to v2. The asymmetric risk — broken cleanup leaving orphaned cloud resources the user pays for — outweighs the v1 value of automated apply validation. `cyberlab-gen fix` is the v1 mechanism for runtime issues, keeping the user in the loop for every patch.

### 0.7 The "lab class is emergent" principle

The architecture deliberately does *not* pre-classify labs into shapes ("reproducible TTP chain lab," "vulnerability primitive lab," "investigation lab," etc.). Lab shape is **emergent** from per-step decisions made during extraction and generation.

The mechanism: every chain step carries a per-step `reproducibility` classification (`full` / `partial_simulation` / `demonstration_only` / `not_reproducible`). The generator, working step by step, applies the preference ordering from `schema.md §4.20` — preferring full reproduction, falling back to partial simulation only when full is genuinely not achievable, falling back to demonstration only when neither is possible, dropping the step from the lab when even demonstration would not be meaningful.

**Classification authority.** Per-step reproducibility is assigned by the **Extractor** based on what the blog describes (a step that's "post-incident commentary" gets `not_reproducible`; a step that's "destructive payload demonstration" gets `demonstration_only`). The **Planner** carries these classifications forward into the Manifest without modification. The **Generator** does *not* re-evaluate classifications. If the Generator hits a capability boundary that prevents implementing a step as classified, it fails the stage with structured feedback; the refinement loop routes the failure back to the Planner (and possibly to the Extractor if the issue is at the AttackSpec level). This preserves the §1.5 invariant — LLMs don't override their input frame; the framework routes when reality doesn't fit.

The four levels, briefly:

- **`full`** — real infrastructure, real attack mechanics, faithful to the blog. (Spin up real EC2 with IMDSv1, steal the role credentials.) Preferred.
- **`partial_simulation`** — real infrastructure where possible, simulated where not, with the attack mechanic preserved. (Use Verdaccio for the npm registry because we can't publish malicious packages to real npm; the attack chain through the registry is preserved.)
- **`demonstration_only`** — the step is documented and possibly illustrated with a non-functional script (prints what would happen, doesn't do it). Used for destructive payloads, unrecoverable actions, or operations that are educationally valuable but unsafe to actually perform.
- **`not_reproducible`** — the step is dropped from the lab. Used when including the step would be misleading or pointless (pure attacker tradecraft with no defender lesson, capability the lab can't meaningfully describe, or commentary that isn't really part of the attack chain). The step still appears in the AttackSpec for fidelity to the blog; it does not produce phase, step, or doc content in the lab.

A lab where every step lands at `full` produces a fully reproducible lab. A lab where steps span multiple tiers produces a `mixed` lab — any heterogeneity in per-step reproducibility produces `mixed` at the lab level, regardless of proportions (see `schema.md §4.8` for the derivation rule). A lab where every step is `demonstration_only` produces a docs-heavy lab with minimal provisioned infrastructure. The lab's overall character is a *result* of step-by-step decisions, never a category the agent chose upfront.

This principle has consequences across the architecture: the Planner does not pick a "lab class"; the Generator does not select from a fixed family of templates by class; the Critic does not assess against class-specific rubrics. These are stated where relevant in `agents.md §5` and `validation.md §6`.

**Derived properties are computed and visible, but never authored.** The lab's overall reproducibility, its dominant provisioning mechanism, the mix of platforms it targets — these are computed by walking the per-step decisions and surface in the manifest's `core` block, in the README's "How to use this lab" section, and in the validation report. The Docs Generator adapts to them (a docs-heavy lab gets different doc framing than a fully-reproducible one). The difference from pre-classification is that no agent ever writes "this lab is class X"; the agents make per-step decisions, and the lab's character falls out.

The reason the architecture doesn't enumerate classes: enumeration would force the agent to stuff blogs into pre-shaped buckets. Real-world cloud-relevant blogs vary too much for that to capture them honestly. Per-step decisions, falling back through a documented preference ladder, produce more honest output.

---

## 1. Foundational design decisions

The decisions in this section are the load-bearing architectural decisions for cyberlab-gen. Each section states the decision, then names the consequences elsewhere in the architecture. Justifications focus on non-obvious reasoning — what a senior reviewer might not immediately agree with, rather than what's obviously sensible.

### 1.1 The system has a structured manifest

Every generated lab carries a YAML manifest (`lab.yaml`) describing what the lab is. The manifest is the single source of truth for the lab's structure: phases, lab resources, prerequisites, inputs, outputs, world state, facets declared, reproducibility decisions.

**Why this is non-obvious.** A simpler design would have generated labs as plain code directories with no metadata. The manifest costs additional generation work and adds a structural-validation gate (Layer 1) the system must pass. The justification: every other piece of the architecture coordinates through the manifest — the Generator reads it to write code, the Validator reads it to check consistency, the Critic reads it to assess fidelity, the Docs Generator reads it to write docs, the Cleanup Generator reads it to write cleanup orchestration. Without a structured manifest, every consumer would have to re-derive structure from code, with no canonical answer.

**Lifecycle.** The manifest is produced incrementally across agent stages:

| Stage | What it contributes to the manifest |
|---|---|
| Extractor | Produces AttackSpec, not Manifest. The Manifest is downstream. |
| Planner | Produces the Manifest skeleton: phases, lab resources, prereqs, inputs, outputs, facets, per-step reproducibility. No code paths yet. |
| Per-phase Generator | Adds `implementation.path` for each phase as code is generated. |
| Lab-level Generator | Adds finalized `lab_resources` IaC outputs as actual code is generated. |
| Docs Generator | Adds doc references and confidence summary. |

Structural schema validation runs after each stage. Structurally invalid output is *retried within the stage* (up to the stage's retry budget, default 3); if retries are exhausted, the pipeline halts with a structured error. Quality issues (jury feedback, Critic concerns) go to the refinement loop, which is a separate mechanism — see §1.7 for the distinction.

### 1.2 The schema uses the facet pattern

Lab properties are declared via *facets* — named tags drawn from three categories: `target:*` (what the attack targets), `runtime:*` (what platforms the lab provisions against), `lab_class_signal:*` (cross-cutting characteristics like simulated components, multi-language, parameterized, etc.).

**Why this is non-obvious.** A simpler design would have flat boolean fields (`is_multi_cloud`, `requires_simulation`, etc.). Facets are more verbose but compose better — new properties are added by introducing new facet values, not by extending the manifest's top-level schema. This matters because the lab properties space is open-ended: every new cloud-relevant attack category may introduce new properties.

**Discipline applied to OSS scale.** No bureaucratic governance — no two-reviewer rule, no marginality tiers, no experimental/stable lifecycle. Facets and value types are added either by maintainer PR (for the bundled registry, informed by eval-harness data) or by agent proposal at runtime (for the user overlay, see `schema.md §4.16`). Promotion from overlay to bundled is also a PR — when eval-harness telemetry shows a proposal recurs across blogs and produces working labs, it's a candidate for the bundled registry. The registry is *seeded* for v1 scope and grows by addition; the goal is for the agents to handle novel types via proposal even when the bundled registry doesn't cover them.

### 1.3 The schema uses a typed registry for inputs and outputs

Values that flow between phases (credentials, resource references, tokens, etc.) are *typed* against entries in the `value_types` registry. Each entry has a name, JSON Schema for shape, sensitive flag, examples, and `notes_for_generator` (guidance about known LLM failure modes for this type).

**Why this is non-obvious.** A simpler design would have used free-form strings everywhere. Typing values lets the Validator catch inter-phase mismatches at Layer 2 (phase A declares output type X; phase B expects type Y), lets the Generator emit the right credential-handling code per type, and lets cleanup scripts target the right resources.

**`notes_for_generator` authorship.** This field is written at proposal time (by the Extractor when proposing a new type) and stays with the entry. Agents do not modify `notes_for_generator` of existing entries at runtime — that would create runtime registry churn. Users can edit `notes_for_generator` when reviewing a proposal at the interrupt. Maintainers can update via PR to the bundled registry, informed by eval-harness data.

**Per-entry, not per-lab.** `notes_for_generator` is stable across all uses of an entry. Per-blog context lives in `tradecraft_notes` on chain steps (see `schema.md §4.7`); per-entry hints don't change between labs.

### 1.4 Generation uses four agent types

Code generation is split across four agent types:

- **Per-phase Generator** (one instance per phase; parallelizable when phases are independent — see `schema.md §4.5` for the operational definition of independence) — generates the implementation of one phase.
- **Lab-level Generator** (one) — generates lab-level orchestration (`setup.sh`, lab IaC, entry-point script).
- **Cleanup Generator** (one) — orchestrates per-phase cleanup scripts plus handles cross-phase shared state and lab-level resource teardown.
- **Docs Generator** (one) — generates documentation (README, attack guide, concepts, MITRE/CNAPP mappings, etc.).

These are the four *Generator* agents. The broader agent inventory includes Extractor, Planner, two juries, Critic, and Repair Agent — see `agents.md §5` for the full inventory.

**Why this is non-obvious.** A simpler design would have used a single monolithic generation agent. The four-type split costs orchestration complexity but earns several benefits:

- **Parallelism.** Per-phase Generators run in parallel when their phases are independent. A single agent generating everything serially can't.
- **Bounded context.** Each per-phase Generator sees only its phase plus shared context. A single agent would see the entire lab and would have to reason about everything at once — bigger context, harder to reason about cleanly.
- **Cleanup fidelity.** The phase agent that creates state has the freshest context for cleaning that state up. Per-phase cleanup is owned by the phase Generator; the Cleanup Generator orchestrates rather than re-deriving.
- **Failure routing.** Different generation problems route to different agents. A Docs Generator issue doesn't re-run all the code generation.

**Cross-phase contracts.** Lab-level Terraform output names are *declared by the Planner* in the manifest's `lab_resources` block. The Per-phase Generator references those declared names. The Lab-level Generator is contractually bound to produce outputs matching those names. The Validator's Layer 2 verifies this contract (`validation.md §6.5`).

### 1.5 The orchestrator is deterministic; LLMs are specialist workers

The pipeline is a deterministic state machine with typed cross-stage boundaries. Every stage's output is a typed model validated against the next stage's input. No free-text passes between stages. *(v1 implementation: LangGraph for orchestration, Pydantic AI for typed agents.)*

**LLMs do:**
- Produce content (extraction, planning, generation, docs).
- Produce structured judgments (jury verdicts, Critic verdicts and scores, refinement recommendations).

**LLMs do not:**
- Route control flow between stages.
- Decide their own retry budgets.
- Decide whether to stop the refinement loop.
- Write to or modify shared state outside their designated output.
- Override blog content with API content (the framework does this — see `schema.md §4.9`).
- Decide whether their own output is acceptable.
- Decide whether their output ships to the user.

These decisions are framework code, deterministic and auditable. The split is enforced by tool availability — agents only have tools relevant to their job; routing, retry, stopping, and shipping tools exist only in framework code. The §1.5 invariant says juries judge and the framework acts on those judgments; the framework reads a jury's verdict and decides what to do with it.

**Why this is non-obvious.** A simpler design would have given LLMs broader autonomy ("agent decides if it's done"). The §1.5 split is stricter for a real reason: control-flow decisions made by LLMs are hard to debug, hard to audit, and tend to fail in correlated ways across runs. Determinism in the orchestrator means failures route predictably and the system's behavior is reproducible at the structural level even when LLM outputs vary.

### 1.6 Validation has multiple layers; only the Critic uses LLM judgment

Validation is split into mechanical layers (deterministic checks running in code) and the Critic (an LLM-based holistic assessment running as a peer to the validator, not as a layer within it).

**Mechanical layers in v1** (see `validation.md §6`):

1. **Layer 1** — static schema validation.
2. **Layer 2** — semantic cross-check (declarations vs. implementations).
3. **Layer 3** — containerized dry-run.
4. **Layer 4** — *v2-deferred*. Real-platform apply validation, with stricter safety boundaries when it returns in v2 (see §8 rationale).
5. **Layer 5** — safety scans, including the canonical lab-credentials catalog whitelist.

The Critic runs after the mechanical layers complete. It produces per-dimension rubric scores, per-phase confidence, and a verdict (`approve` / `refine` / `reject`).

**Mechanizable safety-critical checks are mechanical, never LLM-based.** Layer 5 high-severity findings halt the pipeline; no LLM is asked to judge whether credential content is "really" a leak. The cleanup-confidence gate (§0.5 criterion 2) is the same pattern at a different point in the lifecycle — when the Critic's per-phase confidence for cleanup-relevant phases falls below threshold, the generated `setup.sh` mechanically refuses to run without explicit acknowledgement, rather than relying on the user to read flags. Some safety properties (like "this isn't a phishing kit") are not fully mechanizable and rely on the layered safety model — scope (§0.2), ingestion notices (`pipeline.md §3.1.1`), mechanical credential/host-attack scans (Layer 5), and the Critic together provide defense in depth, not perfect prevention.

**Critic verdict semantics.**
- Critic `approve` + mechanical layers pass → lab ships with high confidence.
- Critic `refine` → feeds refinement loop's stopping decision; budget permitting, refine and re-check.
- Critic `reject` after exhausted refinement → lab still ships, with the rejection prominently surfaced in `validation-report.md` and per-phase confidence flags in the README. The user decides whether to use the lab (with `cyberlab-gen fix` for runtime issues) or regenerate.

The Critic is advisory. Its verdict never directly blocks shipping a lab. The only mechanical-fail case that blocks shipping is Layer 5 high-severity (`validation.md §6.8`).

### 1.7 Refinement is bounded and strategy-pluggable

When validation or jury feedback flags issues, the system refines by **targeted patch**, not blind re-extraction. The framework hands the responsible agent the prior artifact plus the *structured* findings (typed and field-level — see `schema.md §4.9`); the agent returns a **patch** supplying new content and provenance for **only the flagged field paths**. The framework deep-sets that patch onto a copy of the prior artifact and re-validates, so every unflagged field stays byte-identical. Refinement is therefore **convergent by construction** — touching only flagged paths cannot regress an unflagged one, which is exactly the failure mode (a quality score bouncing 9→6→9→10) of re-rolling every field each pass. The one exception is the artifact-level natural-language-feedback path in interactive mode, where the user rejects the whole artifact rather than naming fields; there a from-scratch re-run is correct. Refinement is bounded by configurable caps:

- **Total LLM cost cap** (default $10; configurable via `--max-llm-cost` or config).
- **Total iteration cap** (default 20).
- **Per-agent iteration cap** (default 5).

When the next iteration's estimated cost would push spend past the cap, the budget-overrun interrupt fires in both interactive and auto modes. The user can raise the cap, abort, or explicitly proceed past the cap.

**Retry vs. refinement.** Two different mechanisms:

| Aspect | Retry | Refinement |
|---|---|---|
| Trigger | Stage-internal failure (timeout, schema-invalid, malformed) | Downstream judgment (jury, validator, Critic) |
| Input | Same as original call | Prior artifact + typed structured findings; agent returns a patch of flagged fields |
| Budget | Stage-local (default 3 attempts) | Pipeline-wide (per the caps above) |
| Coordinator | Stage's own retry logic | Refinement loop coordinator (`pipeline.md §3.2.12`) |

Refinement is for *quality* feedback; retry is for *structural* flakiness. Schema-invalid output goes to retry, not refinement. For the full operational breakdown — the four distinct redo mechanisms (transient, malformed-output, grounding/search-before-claim retries, and refinement), each with its trigger and owner — see `validation.md §6.10.1`.

**Stopping strategies are pluggable.** v1 ships three: fixed-N iterations (baseline), score plateau, and validator+Critic verdict. The eval harness compares them; users can select via config. See `eval.md §7.7` for the comparison methodology.

**Placeholder caps pending eval-harness data.** The $10 / 20 / 5 defaults are starting points. The first eval-harness run measures actual usage on the curated set and produces calibrated values for v1 release. Users may need to raise caps in the interim; see §8 for items requiring empirical data before locking.

**Two budgets in v1, not three.** LLM token budget (paid to providers, per this section) and external API call cap (operational concern, see `pipeline.md §3.2.4`). Cloud resource budget is v2-bound — it doesn't apply in v1 because Layer 4 (the only stage that would spend cloud money) is v2-deferred.

### 1.8 The eval harness is a peer of the pipeline, not an afterthought

The eval harness is part of the codebase, co-located with the pipeline. Its blog sets, metrics, strategies, and reports live in the repo. It runs in CI on pull requests, runs deeper checks on release candidates, and produces release-eval reports.

**Honest framing built in.** The eval harness explicitly acknowledges that held-out-set integrity is best-effort, not absolute — the blog set is public, maintainers can read it, contamination is a real possibility. Rotation per release moves blogs between curated and held-out sets to enforce structural distance over time. See `eval.md §7.2` for the full framing.

**Why this is non-obvious.** A simpler design would have built generation first and added evaluation later. The §1.8 commitment changes generation development: every architectural decision can be empirically compared rather than chosen by intuition. Strategy parameters, stopping rules, prompt structures, registry seeding — all are evaluable.

---

## 2. Top-level system map

### 2.1 User-surface commands

cyberlab-gen exposes four CLI verbs:

- **`generate <url>`** — the headline path. Takes a blog URL, runs the generation pipeline, produces a lab directory. Modes: `--interactive` (default; pauses at typed-artifact interrupts) and `--auto` (no interrupts except budget-overrun). See `pipeline.md §3.1`.
- **`validate <lab-dir>`** — runs mechanical validation layers against an already-generated lab. Refuses old-schema artifacts with a "regenerate from blog URL" message. Used in CI or after manual edits to a lab. Most users never invoke it directly; their generated labs were already validated during `generate`.
- **`fix <lab-dir>`** — interactive REPL for post-generation debugging. The user describes problems they encountered running the lab; the Repair Agent proposes minimal patches, asks clarifying questions, or explains when the problem is in the user's environment. The Repair Agent has no write access (framework applies patches only after user approval), no cloud access, and no lab execution; the user reviews every patch before application. See `pipeline.md §3.4` and `agents.md §5.16` for the full boundary specification.
- **`telemetry submit`** — sends queued local reports (after sanitization preview) to the project's endpoint. See `pipeline.md §3.6`.

### 2.2 Properties of the system

**1. The pipeline is a deterministic state machine.** Every stage has typed inputs and outputs. The orchestrator's runtime state is the canonical record of what's happened. No free-text passes between stages; everything is structured.

**2. The bundled registry is read-only at runtime; the user overlay is writable.** The bundled registry ships at `<install-dir>/registry/`. The user overlay lives at `~/.cyberlab-gen/registry-overlay/`. Agent proposals at runtime land in the overlay. Both are merged at runtime; overlay wins on name collisions. See `schema.md §4.11` for the full model.

**3. The eval harness is separable but co-located.** The harness can run independently of generation, but it lives in the same repo and uses the same schemas and registries. Co-location prevents the harness from drifting away from the system it evaluates.

**4. Labs are decoupled from the tool after generation.** A generated lab is a folder on the user's disk. It does not invoke cyberlab-gen at runtime. Its `setup.sh`, `cleanup.sh`, and `verify.sh` run against the user's environment using only standard tools (Terraform, cloud CLIs, etc.) — the user does not need cyberlab-gen installed to run a lab they previously generated. The lab is the user's artifact, not the tool's. The tool's only post-generation interaction with a lab is via `validate <lab-dir>` (refuses labs whose `spec_version` doesn't match the tool's current schema, pointing the user to re-generate from the original blog URL) and `fix <lab-dir>` (interactive debugging). This decoupling is what makes the no-migration rule, the registry overlay model, and the immutable-artifact framing all coherent.

### 2.3 System diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                      USER INTERFACE (CLI)                            │
│   generate <url>     fix <lab-dir>     validate     telemetry submit │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       GENERATION PIPELINE                            │
│  (deterministic state machine, typed cross-stage boundaries)         │
│                                                                       │
│   Ingestion → Extractor → Extractor-Jury → Enrichment →              │
│   [interrupt] → Planner → Planner-Jury → [interrupt] →               │
│   Per-phase Generator (parallel) → Lab-level Generator →             │
│   Cleanup Generator → Docs Generator → Validator → Critic →          │
│   Refinement loop → Output                                            │
└──────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     GENERATED LAB (on disk)                          │
│   Decoupled from cyberlab-gen. Standalone artifact.                  │
│   setup.sh / cleanup.sh / verify.sh / phases / docs / manifest       │
└──────────────────────────────────────────────────────────────────────┘
                                 │
                                 │  (user runs lab; may hit runtime issues)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          FIX PIPELINE                                │
│   Interactive REPL with Repair Agent. User describes problems;       │
│   agent proposes patches; user reviews and applies. Cross-session    │
│   continuity via fix_history.json.                                   │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│              BUNDLED REGISTRIES (read-only at runtime)               │
│   value_types.yaml | facets.yaml | external_data_sources.yaml        │
│   lab_credentials.yaml (canonical fakes, bundled-only catalog)       │
└──────────────────────────────────────────────────────────────────────┘
                                 │
                                 │  merged at runtime
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│         USER OVERLAY at ~/.cyberlab-gen/registry-overlay/            │
│         (writable; agent proposals land here)                        │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│            LOCAL STATE at ~/.cyberlab-gen/                           │
│   config.yaml | cache/ | checkpoints/ | runs/ | reports/             │
│   reports submitted explicitly via `telemetry submit`                │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│           EVAL HARNESS (peer to pipeline, co-located)                │
│   Curated set + held-out set with rotation per release               │
│   Mechanical metrics + Critic scores + variance reporting            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 8. Open questions and deferred decisions

A few things this document deliberately did not lock down. These are recorded so they don't drift into "implicitly decided" without scrutiny.

### 8.1 Architectural decisions log

Major v1 decisions made during architecture design, recorded here so the reasoning is preserved:

- **Always-ship-with-confidence-flags vs. abandon.** Generation almost always ships *something*: high-confidence labs with clean validation, mixed-confidence labs with detailed flags, low-confidence labs with honest reports. True abandonment is reserved for cases where no coherent artifact was produced. **Rationale:** the user spent money on generation; throwing the work away because the Critic disagreed is wasteful and dishonest about what we have. The user decides whether to try the lab and whether to invest in `fix`.

- **`cyberlab-gen fix` in v1.** Post-generation debugging mode included in v1 scope rather than deferred. **Rationale:** real-world labs fail at runtime for reasons the generator can never anticipate (the user's specific IAM policies, region availability, package version mismatches, Terraform syntax drift). A debugging mode is part of being honest about that. `fix` keeps the user in the loop for every patch — no autonomous execution, no cloud API access.

- **Scope enforced by agent judgment, not registry coverage.** The registries are *prior knowledge*, not *permission*. Agents propose new entries when they encounter novel values; scope refusals happen at the Extractor (out-of-scope content) and Planner (unplanable infrastructure), not at registry-lookup time. **Rationale:** registry-as-gate would refuse labs for novel attacks on novel technologies that the agents could in principle reason about coherently. The system's real limits are what agents can produce, what validators can check, and what platforms exist — not what's in the seeded registry.

- **Layer 4 deferred to v2.** Real-platform apply validation removed from v1. **Rationale:** asymmetric risk. When cleanup is broken (hallucinated resources, missing permissions, race conditions), Layer 4 leaves orphaned cloud resources the user pays for. The system should not modify the user's real cloud without the user being actively in the loop. `cyberlab-gen fix` is the v1 mechanism with the user in the loop for every patch. Layer 4 may return in v2 with stricter safety boundaries (mandatory pre-apply confirmation, mandatory post-apply verification before any cleanup, refusal to apply when cleanup is structurally incomplete).

- **Open-runtime model with first-class flag.** `runtime:*` is open-set; the Planner can propose new runtime entries; proposed entries are `first_class: false` and generate labs with reduced coverage flags. **Rationale:** refusing to generate labs for Cloudflare, Vercel, or other non-Big-Three runtimes contradicts the scope-by-agent-judgment principle. First-class status is about *coverage quality*, not *whether a lab can be generated*.

- **`__unknown__` value-type fallback removed.** Always type values, never fall back to untyped placeholder. **Rationale:** shipped degraded labs without integrity guarantees. The Extractor must propose a real registry entry when no existing one fits; there is no third option that produces a coherent lab.

### 8.2 v1.5+ scope items (named, not specified)

- **Researcher stage** — augment AttackSpec with external research when blog content is incomplete. Current seam: `unknown_from_blog.reason` indicating "requires external research."
- **Multi-path generation** — generating alternative paths beyond the canonical (the schema captures them in v1).
- **Value-type modifiers** — `validity_window`, `single_use`, `revocable` etc. Added when a real lab can't generate without them.
- **Defender-mode and observer-mode lab classes** — `lab_class_signal:actor_scoped:defender` and `:observer` reserved in the registry; v1 generators refuse them.
- **Local-model adapter quality testing** — the `Provider` interface supports it; quality is not v1-tested.
- **Registry browser UI / CLI subcommand** for browsing the registries.
- **`cyberlab-gen fix --regenerate-phase`** — from a fix session, regenerate a specific phase rather than patching it. v1 fix mode patches only; regeneration directs the user back to `cyberlab-gen generate`.
- **Fix-pattern learning loop** — automated promotion of recurring successful fix patterns to prompt overlays or registry `notes_for_generator`. v1 keeps this manual (maintainer review).
- **Fix-session continuity refinements** — auto-detect when prior fix history is stale because the user re-ran generate.
- **Runtime promotion to first-class** — moving a proposed runtime to first-class status requires Layer 4 verification logic, credential check conventions, and confirmation friction; this is a maintenance activity in v1.5+.

### 8.3 v2 scope items (named, not specified)

- **Layer 4 (real-platform apply validation)** with stricter safety boundaries. The v1 deferral is documented in §8.1 above; the v2 design considerations (mandatory pre-apply confirmation, post-apply verification before cleanup, refusal-on-incomplete-cleanup) are sketched in `validation.md §6.7`.

### 8.4 Items requiring empirical data before locking

The following are v1 placeholders to be calibrated. They are organized into two tiers reflecting when they must be locked.

**Pre-release calibration (must be informed before v1 ships).** These placeholders affect first-run user experience too directly to ship as the architecture's current "we know these are wrong" values. The pre-release calibration runs on 2-3 curated-set blogs with current placeholders, measures actual usage, and replaces the placeholders with informed defaults. Statistical validation comes later; informed defaults come before launch.

- **Default refinement-loop caps** (currently placeholder $10 / 20 iterations / 5 per agent — likely need raising for multi-phase labs based on first-cycle eval data).
- **Default per-agent token budgets** (currently placeholder values in `agents.md §5.19`; sum exceeds the cap, indicating one or both need tuning).
- **Fix-mode budget default** (currently $5 placeholder).

**Post-launch calibration (refinable from telemetry-aggregated eval data over time).** These have starting values informed by the curated set or by clean architectural reasoning, but their long-run optimums depend on real-world distribution.

- **Default jury approval thresholds** (currently 0.7 floor, 2 retries). Asymmetric calibration: tune *upward* on observed false-approval, never symmetrically downward on false-rejection (see `agents.md §5.5` and `eval.md §7.5`).
- **Default stopping strategy choice** (currently fixed-N as baseline).
- **Coefficient-of-variation threshold for high-variance flagging** (currently 0.3).
- **Per-run cap on auto-accepted registry proposals** (currently 5; may need adjustment based on observed proposal patterns).
- **Fix-mode validation strictness** (Layer 3 auto-runs on IaC patches per `pipeline.md §3.4.4`; the `--validate-patches-thoroughly` flag controls the other cases).
- **Critic web_search call budget** (v1 placeholder 5 calls per Critic run, framework-tracked; exceeding the cap fails the stage rather than relying on prompt-level "sparingly" discipline).
- **User-confirmation confidence threshold** (currently 0.6 placeholder; suggested starting value, pending eval data). When `source == llm_inference` and `confidence` is below this threshold, the framework sets `requires_user_confirmation: true` on the provenance metadata, and the post-Extractor / post-Planner interrupts surface the field for per-field review (see `schema.md §4.9` for the flag's semantics).
- **Tool-use loop `max_iterations` per agent** (no v1 default; per-agent placeholders pending eval data). Each tool-using agent declares the maximum number of tool-call iterations the provider's `complete_with_tools` loop will run before raising `ProviderError.ToolLoopError` (routed to refinement-or-abandon per `pipeline.md §3.2.12`). Initial placeholders: Extractor 5, Planner 5, Critic 6 (informed by the Critic web_search budget above), Repair Agent 10. See `provider-interface.md §13.1` for context.

**Per-phase confidence presentation (locked, not pending data).** The presentation pattern is locked even though the exact threshold values will be calibrated:

- Confidence ≥ 0.6: standard surfacing in the README's "How to use this lab" section.
- Confidence < 0.6 and ≥ 0.4: surfaced with "low confidence — recommend reviewing this phase before running" framing.
- Confidence < 0.4: surfaced with "low confidence — recommend regenerating" framing.
- Cleanup-relevant phases with confidence < 0.5: the mechanical setup.sh gate fires (see §0.5 criterion 2).

The three-tier presentation is the architectural commitment; the exact threshold numbers (0.6, 0.5, 0.4) are tunable post-launch. The README's confidence summary uses these tiers consistently across labs so users develop reliable mental models.

### 8.5 Items considered and rejected

- **Capabilities facets** (`produces_capabilities`, `requires_capabilities`) — redundant with `produces_world_state`.
- **`external_api_corrected_blog` as a separate provenance source** — the existing `external_api` with both citations covers it.
- **Pre-classified lab classes** — emergent classification per §0.7 covers it more honestly.
- **Schema version migration tooling** — labs are immutable on disk; tool refuses old-schema artifacts.
- **Multi-lab generation from one blog** — chaptered long labs with `--from-phase` cover the use case.
- **Reason-code enum for `unknown_from_blog.reason`** — free-form string is the v1 form; telemetry aggregation may inform stabilization later.
- **`__unknown__` value-type placeholder** — shipped degraded labs without integrity guarantees. Replaced with always-propose discipline.
- **`--auto-extend-registry` confirmation flag** — theater, since labs are decoupled from the registry after generation.
- **`revalidate <lab-dir>` command** — no real use case once labs are understood as decoupled from the tool after generation.
- **Planner authority to propose value types** — concentrated in the Extractor only.
- **Layer 4 in v1** — see §8.1 for rationale.
- **Unlisted publisher notice** — low marginal value; Extractor's out-of-scope path is the real source-quality filter.
- **`local_simulator` and `runtime:multi` as bounded entries** — redundant with the open-runtime proposal model. Multi-platform labs simply declare multiple `runtime:*` facets.
- **Per-step `bind_inputs`** — step-to-step value flow is handled by phase composition mode (sequential threads outputs; independent keeps results per-step).

### 8.6 Companion documents

The architecture references implementation details that live in companion documents drilled down from this architecture. Some exist; some are deferred:

**Created:**

- `schema-details.md` — exact field-by-field Pydantic shapes, validators, and Python module layout for every architectural model.
- `registry-details.md` — full v1 entries for each registry, with shape commentary keyed to the schema-details Pydantic models.
- `provider-interface.md` — the `Provider` ABC, capability hints, cost ledger, retry strategy, and adapter contract.
- `implementation-plan.md` — sequenced phasing for building cyberlab-gen v1 from zero to release.
- `coding-conventions.md` — Python conventions, tooling (uv, ruff, pyright), type discipline, testing conventions, error handling.

**Deferred to a later design phase:**

- `prompts.md` — agent prompts and canonical code-shape examples (produced in Phase 1 from `dev/prompt-iterations/`).
- `validator-rules.md` — exact rule sets per layer, severity floors, scanner configurations (produced as validators ship in Phases 1–3).
- `setup-style-guide.md` — conventions for setup.sh, cleanup.sh, verify.sh (produced when the Generators ship in Phase 3).

These deferred items have explicit seams in the v1 architecture, so adding them later doesn't require breaking changes.

---

## 9. Document conventions

### 9.1 Terminology

- **chain_step** — a unit in the AttackSpec's `chain.chain_steps` mirroring the blog's narrative.
- **phase** — a unit in the Manifest's `phases` representing an implementation module.
- **step** — a unit within a phase block, representing a narrative + execution unit.
- **facet** — a declaration in the bundled `facets` registry, organized into categories (`target:*`, `runtime:*`, `lab_class_signal:*`).
- **provenance** — the structured `{value, source, citations, confidence}` metadata on every content field.
- **artifact** — a structured YAML output (AttackSpec or Manifest) with versioned schema.
- **lab class** — emergent property of a lab, not pre-classified. The result of per-step decisions per `schema.md §4.20`.
- **first-class runtime** — a runtime with built-in Layer 4 verification logic, credential check conventions, and cleanup support. v1: AWS, Azure, GCP, GitHub.
- **fix session** — a single invocation of `cyberlab-gen fix <lab-dir>`. The REPL maintains live conversation context for the session; cross-session continuity comes from `fix_history.json`.

### 9.2 YAML examples and field shapes

YAML examples in this document and in `schema.md` use illustrative content. Exact field-by-field shapes (every nullable, every constraint, every JSON Schema definition) live in `schema-details.md` (planned companion).

### 9.3 Cross-references

- **Within a file:** section numbers (e.g., "see §4.20" within `schema.md`).
- **Across files:** file name plus section number (e.g., "see `pipeline.md §3.2.6`").

### 9.4 Identifier conventions

- **ID values** use kebab-case (`phase-1-initial-access`, `chain-step-3`, `defender-technique-1`). User-facing in CLI flags and cross-reference fields within manifests.
- **Field names** use snake_case (`step_composition`, `bind_inputs`, `produces_world_state`). YAML/Python convention.
- **Filesystem paths** use snake_case (`attack/phase_1_initial_access/`). Python module names must be valid Python identifiers; kebab-case is invalid for Python.
- **Conversion is mechanical.** ID `phase-1-initial-access` lives at filesystem path `attack/phase_1_initial_access/`. The Generator does the conversion; the manifest carries the kebab ID and derives the path.

### 9.5 Acronym glossary

- **CSPM** — Cloud Security Posture Management. Misconfiguration detection across cloud accounts.
- **CWP** — Cloud Workload Protection. Runtime protection of compute workloads (VMs, containers).
- **CDR** — Cloud Detection and Response. Real-time threat detection in cloud environments.
- **CIEM** — Cloud Infrastructure Entitlements Management. Permission and entitlement analysis.
- **DSPM** — Data Security Posture Management. Discovery and protection of sensitive data at rest.
- **ASPM** — Application Security Posture Management. Security state across the SDLC.
- **ITDR** — Identity Threat Detection and Response. Identity-tier compromise detection.
- **KSPM** — Kubernetes Security Posture Management. Misconfiguration detection in Kubernetes.
- **CNAPP** — Cloud-Native Application Protection Platform. Umbrella covering CSPM + CWP + CIEM and others.
- **MITRE ATT&CK** — Adversarial Tactics, Techniques, and Common Knowledge. Threat framework.
- **NVD** — National Vulnerability Database. Authoritative source for CVE metadata.
- **MSRC** — Microsoft Security Response Center. Authoritative for Microsoft-issued CVEs.
- **KEV** — Known Exploited Vulnerabilities. CISA catalog of vulnerabilities seen in real-world exploitation.
- **EPSS** — Exploit Prediction Scoring System. Probabilistic model of exploitation likelihood.
- **OSV** — Open Source Vulnerabilities. Cross-ecosystem vulnerability database.
- **PoC** — Proof of Concept. In vulnerability context, a working exploit demonstrator.
- **IaC** — Infrastructure as Code. Declarative resource provisioning (Terraform, CloudFormation, etc.).
- **IMDS** — Instance Metadata Service. Cloud VM metadata endpoint accessible from inside the VM.
- **PAT** — Personal Access Token. GitHub or similar.
- **SCP** — Service Control Policy. AWS organization-wide IAM policy.
- **RBAC** — Role-Based Access Control. Generic access control model; specifically used by Azure for cloud resource access.
- **IAM** — Identity and Access Management. AWS and GCP terminology for identity/permission systems.
- **DAG** — Directed Acyclic Graph. Used here for phase dependency structure.
- **REPL** — Read-Eval-Print Loop. The interactive shell for `cyberlab-gen fix`.

---

*End of architecture hub document. See `pipeline.md`, `schema.md`, `agents.md`, `validation.md`, and `eval.md` for the rest of the architecture.*
