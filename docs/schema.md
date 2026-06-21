# cyberlab-gen — Schema and Registries

**Companion to:** `architecture.md` (hub).
**Document scope:** The structured artifacts the system produces and consumes — the **lab Manifest** and the **AttackSpec** — and the **registries** they reference. Specifies what each artifact is for, what blocks it contains, what registries exist, how they compose, and how the schema evolves over time.

Exact field-by-field shapes (every nullable, every constraint, every JSON Schema definition) live in `schema-details.md` (planned companion). This document is the architectural layer.

---

## 4. Schema and Registries

### 4.1 What this section covers

This section defines the structured artifacts the system produces and consumes — the lab manifest and the AttackSpec — and the registries they reference. It specifies what each artifact is for, what blocks it contains, what registries exist, and how they compose. Field-level YAML examples appear inline with the prose.

The decisions in this section are the load-bearing data-model decisions for the entire system. Agents, validators, and generators all coordinate through these artifacts. Getting them right matters more than getting any single agent prompt right.

### 4.2 Two structured artifacts, two roles

The system produces two structured YAML artifacts during the pipeline. They are *not* the same shape and they serve different purposes.

**AttackSpec** — produced by the Extractor stage. Describes *what the source blog says happened*. It mirrors the blog's narrative structure, not the lab's implementation structure. The chain in an AttackSpec has as many `chain_steps` as the blog tells; the steps may not be cleanly groupable into implementation phases yet. The AttackSpec is a contract between "reading the blog" and "designing the lab."

**Lab manifest** — produced by the Planner stage and refined by Generator stages. Describes *what the lab is*. Its phases are implementation units (each becomes a code module). Its lab resources are concrete cloud or platform resources to provision. Its inputs and outputs are typed values flowing at runtime.

The Planner is the bridge: it consumes an AttackSpec and produces a draft manifest. Some chain steps in the AttackSpec become phases. Some become steps within phases. Some become lab resources (pre-existing world state, not produced by phases). Some become manual prerequisites (`pre_lab` or `mid_lab` timing). Some become demonstration-only or get dropped because they're not lab-reproducible.

**Both artifacts are versioned independently.** The schema for each evolves over time. The system records `spec_version` (integer, monotonic) and `spec_kind` (discriminator) on every artifact. The discriminator is enforced at static-schema validation: loading an AttackSpec where a Manifest is expected (or vice versa) fails loudly with structural error.

In practice the two schemas evolve together as a coordinated release; the independent version fields are a defensive measure to catch artifact-type mismatches loudly, not a promise to maintain divergent version histories.

**v1 schemas are frozen for v1.** The tool reads only current-schema artifacts. Old artifacts produce a clear "regenerate from blog URL" message rather than any migration attempt (`architecture.md §0.6`).

### 4.3 Lab decomposition: three levels

A lab decomposes into three levels, in this order:

1. **Lab** — the whole scenario. One blog, one lab, one manifest. Has a single id, a single overall thesis, a single source citation.

2. **Phase** — an implementation unit. The Generator emits one code module per phase. A phase has a stable function signature (`run_phase(config) -> dict`), a declared composition (`sequential` or `independent`), declared inputs and outputs, and contains one or more steps.

3. **Step** — a narrative + execution unit inside a phase. A step carries MITRE technique mapping, detection signals, description, and runtime banners (narrative/educational), *and* it executes one or more commands or operations that advance the attack (execution). The Generator emits one function per step within the phase module. (For the function signature and the relationship between steps and phase `run_phase`, see §4.7.)

The system **does not** define a fourth level (e.g., "actions" inside steps). Below the step level is just code. MITRE granularity, detection mapping, and the educational walkthrough all live at the step level; there is no field that requires finer granularity.

**Terminology.** Throughout this document:
- A **chain_step** is a unit of the AttackSpec's chain (the blog's narrative). Mirrors the blog.
- A **phase** is a unit of the lab Manifest. The Planner's grouping of chain_steps into implementation units.
- A **step** (within a phase) is a unit of the Manifest's phase block. The Planner's narrative-execution unit.

The rename to `chain_step` for the AttackSpec level removes the same-word-different-level ambiguity earlier drafts had.

### 4.4 The lab manifest envelope

The manifest is one YAML file (`lab.yaml`) at the root of the generated lab. The high-level shape:

```yaml
spec_version: 1
spec_kind: LabManifest
core: <CoreBlock>
facets: [<FacetReference>, ...]
prereqs:
  pre_lab: [<PrereqBlock>, ...]
  mid_lab: [<PrereqBlock>, ...]
inputs: [<InputBlock>, ...]
lab_resources: [<LabResourceBlock>, ...]
phases: [<PhaseBlock>, ...]
outputs: [<OutputBlock>, ...]
extras: [<ExtrasBlock>, ...]
```

#### Top-level blocks

**`spec_version`** — integer, monotonic. Required.

**`spec_kind: LabManifest`** — discriminator. Required.

**`core`** — non-optional metadata about the lab:

```yaml
core:
  id: shai-hulud-2-0
  name: "Shai Hulud 2.0: NPM Supply Chain Worm"
  source:
    url: "https://..."
    author: "..."
    published_at: "2025-..."
  mitre_tactics: [TA0001, TA0003, TA0005]
  thesis: <provenance-wrapped string>
  severity:
    value: High
    source: blog_explicit
    citations: [...]
  cve_references: [...]                  # each with provenance
  reproducibility:                       # structured block, mirrors AttackSpec
    classification_lab_level: mixed      # derived from per-step values, not authored
    caveats: <list of caveat strings>
    overall_assessment: <provenance-wrapped string>
    derivation_trace: <list of which steps' tiers led to the lab-level classification>
  generation:
    tool_version: 1.0.0
    model: <model-id>                    # whatever the provider layer picked
    timestamp: 2026-05-12T...
```

The `reproducibility` block here mirrors the AttackSpec's `ReproducibilityBlock` shape (`§4.8`). The `classification_lab_level` is derived from per-step values per `§4.20` and the heterogeneity rule in `§4.8` (any heterogeneity → `mixed`); the `caveats` field surfaces which tiers are present in what proportions for `mixed` labs. **Affected platforms are not duplicated here** — they're derivable from the `facets` block's `target:*` entries; the Docs Generator and Validator read facets as the authoritative source rather than a separately-authored field.

**`facets`** — multi-value list of facet references. References entries in the facets registry. Categories: `target:*` (what platforms/technologies the attack targets, blog-derived), `runtime:*` (lab-derived runtime properties), `lab_class_signal:*` (cross-cutting characteristics like simulated components, multi-language, reproducibility signals — split between blog-derived and lab-derived; see §4.13). See §4.16 for which agent proposes which category.

**`prereqs`** — split into `pre_lab` and `mid_lab`. Each prereq has a kind (`manual` / `auto_fixable` / `automatic`), a check command, a fix command (where applicable), and a consent prompt. The setup orchestrator reads `pre_lab` prereqs at startup; phases reference `mid_lab` prereqs by ID and the orchestrator surfaces them at the right point during execution.

```yaml
prereqs:
  pre_lab:
    - id: aws-credentials-configured
      description: "AWS credentials configured for the lab account"
      kind: manual
      check_command: "aws sts get-caller-identity"
      consent_prompt: "Do you have AWS credentials configured for a lab account? [y/N]"
  mid_lab:
    - id: delete-attacker-account
      description: "Delete the disposable GitHub attacker account to demonstrate evasion"
      kind: manual
      timing: mid_lab
      applies_to_phase: phase-5-post-attack-cleanup
      consent_prompt: "Manually delete the GitHub account 'lab-attacker-XXXX' now? [y/N]"
```

`mid_lab` prereqs are scoped to phase boundaries (`applies_to_phase`). Step-level pause-and-prompt behavior is not a v1 prereq mechanism; if a step needs the user to do something mid-execution, that's captured in the step's docs/banners, not as a separate prereq.

**`inputs`** — typed values the user provides at lab-run time. Each input has a name, value type (referenced from the value_types registry), source, and default where applicable. Sources:

- `user_config` — from `~/.cyberlab-gen/config.yaml` (per-user persistent config).
- `cli_flag` — passed at lab-run time (e.g., `./setup.sh --target-region us-east-1`).
- `cli_flag_or_default` — `cli_flag` with a fallback default.

**`lab_resources`** — pre-existing world state the lab provisions for the attack to operate against. These are not produced by phases; they exist because the lab's setup created them. Examples: a public S3 bucket with planted credentials, an EC2 instance with IMDSv1 enabled, a vulnerable Lambda function, an Entra ID tenant with a configured service principal, a GitHub organization with an Actions workflow. Each lab resource has a type, an *intended* IaC resource type (declared by the Planner), a `provisioning_mechanism` (declared per-resource — Terraform by default, with fallbacks per §4.20), and a **`lab_role`** declaration. The Lab-level Generator translates these declarations into actual IaC.

**`lab_role`** is a *list* of role values declaring what the resource is doing in the lab. Values:

- `attack_target` — the resource is intentionally configured for the attack to exploit (e.g., a deliberately public S3 bucket, a deliberately over-permissioned IAM role). Security scanners will fire on these; the containerized dry-run reads `lab_role` and relaxes security-finding strictness for resources with `attack_target` in their list.
- `attacker_infrastructure` — the resource serves the attacker side of the lab (the attacker's VM, the attacker's C2 endpoint).
- `defender_infrastructure` — the resource serves the defender side (a logging bucket where CloudTrail writes, a Sentinel workspace, a CloudWatch log group used for detection).
- `neutral` — the resource exists in the lab but has no attack/defense role (containing VPC, networking plumbing, supporting IAM roles).

A single resource can have multiple roles. A logging bucket that the attack deletes from to cover tracks is `[defender_infrastructure, attack_target]`. A CI runner the attacker uses and then escalates from is `[attacker_infrastructure, attack_target]`. An optional `role_notes` field maps each role to per-role context (e.g., `role_notes.attack_target: "Phase 4 deletes objects to cover tracks"`).

The containerized dry-run treats security findings on a resource as informational rather than failing when the resource's `lab_role` list contains `attack_target`. Findings on resources without `attack_target` in their roles are real signals (see `validation.md §6.6`).

Optional `discovery` block, when present, captures two discovery commands: `shortcut_command` (a fast lookup the lab uses internally, e.g., reading from Terraform output) and `attacker_command` (the realistic discovery technique an attacker would use, surfaced in docs for educational value). These are not redundant — they serve different audiences.

**`phases`** — list of phases, in declaration order. Each phase has its own structured block (defined in §4.5).

**`outputs`** — declared outputs of the lab as a whole. References to `terraform output` (or other IaC equivalent) values, plus references to phase outputs that should be exposed at the lab level.

**`extras`** — escape hatch (see §4.10). Free-form metadata that doesn't fit any of the above blocks.

### 4.5 The phase block

A phase block within the manifest contains:

```yaml
- id: phase-1-initial-access
  name: "Initial access via leaked PAT"
  display_name: "1. Initial Access"
  short_description: "Use the leaked GitHub PAT to gain write access"
  mitre_tactics: [TA0001]
  implements_chain_steps: [chain-step-3, chain-step-4]
  step_composition: sequential          # or independent
  execution_context: attacker_local     # or victim_vm_via_ssh, victim_lambda, ...
  on_dependency_failure: warn           # default; or fail, skip
  bind_inputs:
    - name: target_repo
      type: github_repo_reference
      source_phase_output: phase-0.target_repo
  outputs:
    - name: malicious_workflow_run_id
      type: github_workflow_run_id
  produces_world_state:
    # Static identifier — known at manifest-write time
    - type: aws_s3_bucket
      identifier_kind: static
      identifier: lab-attack-bucket-fixed-name
      description: "Bucket created with deterministic name across runs"
    # Runtime-generated identifier — value not known until phase runs
    - type: github_branch
      identifier_kind: runtime_generated
      identifier_source: phase_outputs.malicious_branch_name
      description: "Branch with random suffix; actual name appears in phase output"
  provisioning_mechanism: cli_scripts
  references_lab_outputs: []
  steps: [<StepBlock>, ...]
  implementation:
    language: python
    path: attack/phase_1_initial_access.py  # post-Generator state; the Planner leaves this null (ADR 0079)
    entrypoint: run_phase
```

**Notable fields:**

- **`implements_chain_steps`** — links the phase back to the chain steps in the AttackSpec it implements. Supports the audit trail and the docs generator's cross-referencing.

- **`step_composition`** — `sequential` or `independent`. Determines the code template the Generator's prompt provides as canonical example. See §4.6.

- **`execution_context`** — where the attack code runs (`attacker_local`, `victim_vm_via_ssh`, `victim_lambda`, `victim_build_container`, `victim_serverless`, `victim_pod`, `github_actions_runner`, etc.). Determines the credential and network setup the Generator's prompt assumes. Values come from the bundled execution-contexts registry; new contexts can be added via maintainer PR or proposed by the Planner at runtime (similar to facet proposals — see §4.16).

- **`provisioning_mechanism`** — `terraform` / `cloudformation` / `arm_template` / `gcp_deployment_manager` / `cli_scripts` / `manual` / `mixed`. Per §4.20: Terraform preferred where supported; fallbacks documented in the manifest with rationale. **Closed enum in v1** — adding a new provisioning mechanism (e.g., Pulumi, Crossplane) is a significant integration effort, not just a registry entry; deferred to v1.5+.

- **`on_dependency_failure`** — `warn` (default), `fail`, or `skip`. Governs typed phase output dependencies (when phase B's `bind_inputs` reference phase A's outputs). The default is `warn` rather than `skip` because for security/learning labs, silent skip lies about whether the chain worked — a user reading green output should know when an upstream phase's failure caused downstream phases to behave differently. `skip` remains available as an opt-in for genuinely optional branches; `fail` for labs where chain integrity is critical. Step-level error tolerance within a phase is governed by the phase's `step_composition` mode (§4.6). The two operate at different levels: phase-level enforces typed contracts; step-level tolerates intra-phase failures based on composition.

- **`produces_world_state`** — list of state changes this phase makes outside the lab's IaC. Used for cleanup generation, validation cross-checks, and `verify.sh` derivation (the Cleanup Generator owns both `cleanup.sh` and `verify.sh`; see `agents.md §5.12`). Each entry declares:
  - `type` — value-type reference from the registry.
  - `identifier_kind` — `static` or `runtime_generated`. **Required.** Distinguishes identifiers known at manifest-write time from identifiers that the phase code generates at runtime (e.g., resource names with random suffixes, timestamped IAM users).
  - For `identifier_kind: static`: an `identifier` field with the literal value.
  - For `identifier_kind: runtime_generated`: an `identifier_source` field pointing into the phase's `run_phase()` return dict (or another known output location) where the actual value will be written at runtime. The Cleanup Generator reads this at cleanup time to get the real identifier.
  - `description` — what state this is and how it was created.

  Without the `identifier_kind` distinction, cleanup code would hardcode placeholder identifiers from the manifest and silently fail at runtime — orphaning resources the user pays for. The semantic cross-check (`validation.md §6.5`) verifies that `identifier_source` references resolve to declared phase outputs.

- **`implementation.language`** — `python` in v1; the field is forward-looking. Alternate languages (Go, JavaScript, Rust) are deferred to v1.5+.

- **`extras`** — phase-level escape hatch. Omitted entirely when empty (rather than serialized as `extras: []`).

**Phase-level independence (for parallelism).** Two phases are *independent* iff they share no declared `bind_inputs` between them (no formal data flow) AND no overlapping `produces_world_state` items (neither phase mutates state the other reads or writes). Both conditions are necessary. The framework computes the phase DAG from the manifest and parallelizes independent phases when invoking per-phase Generators (`pipeline.md §3.2.9`). Independence is a property of the manifest's *phases* (this section, §4.5), distinct from `step_composition` which governs how *steps within a phase* relate (§4.6). The two concepts share the word "independent" but operate at different levels.

### 4.6 Step composition: `sequential` vs `independent`

Two composition modes:

**Sequential.** Steps run in declared order. Each step may consume the previous step's outputs. A step failing typically aborts the phase. The Generator's prompt includes a canonical Python skeleton: a function body that calls each step in sequence and returns aggregated results keyed by domain (`results["bucket_contents"]`, `results["identity"]`).

**Independent.** Steps are conceptually parallel — order may be deterministic for output stability, but no step's success is required for another. Each step gets its own try/except wrapper. The Generator's prompt includes a canonical Python skeleton: a `STEPS` list (`[(id, label, func), ...]`) and a generic `run_phase` that iterates with per-step error capture. Results are keyed by step id.

The schema declares which mode the phase uses; the Generator's prompt picks the matching canonical example. Adding a third mode (e.g., `dag` for explicit dependencies among steps within a phase) is deferred to v1.5+ (`architecture.md §8.2`).

**Step-to-step value flow.** Within a phase, step-to-step value flow is handled by composition (sequential mode threads outputs through the results dict; independent mode keeps results per-step). There is no explicit `bind_inputs` at the step level — the phase's composition mode defines the flow.

Canonical Python skeletons for each composition mode are documented in the Generator's prompt overlays (implementation detail, not architecture).

### 4.7 The step block

A step block within a phase contains:

```yaml
- id: step-1-discover-bucket
  step_number: 1                              # or "1.1", "1.2" for sub-numbered
  title: "Discover public S3 bucket"
  description: <provenance-wrapped string>
  function_name: discover_bucket
  mitre_techniques: [T1530]
  detections:
    - component: CSPM
      severity: Medium
      description: <provenance-wrapped string>
      soc_view: <provenance-wrapped string>
      remediation: <provenance-wrapped string>
      formats:
        - format: sigma
          path: detection/phase_1/sigma.yml
        - format: kql
          path: detection/phase_1/sentinel_kql.yml
  reproducibility:                            # carried forward unchanged from the ChainStep (ADR 0081)
    classification: full                      # full | partial_simulation | demonstration_only | not_reproducible
    caveats: <provenance-wrapped string>
    why: <provenance-wrapped string>
  cli_equivalent:
    - "aws s3 ls s3://target-bucket --no-sign-request"
  outputs:
    - name: bucket_listing
      type: aws_s3_object_listing
  tradecraft_notes:
    - name: aws:anonymous-listing
      description: "Used --no-sign-request to avoid logging the discovery"
      evades_what: "Identity-based access logging"
```

(The `extras` field exists on step blocks too as a phase-level escape hatch; it is omitted from the example when empty.)

**Detection component enum (v1, closed): `CSPM, CWP, CDR, CIEM, DSPM, ASPM, ITDR, KSPM, API_Security, Supply_Chain_Security`.**

**Severity enum (v1, closed): `Critical, High, Medium, Low`.**

**Detection formats list (v1, closed): `sigma, kql, spl, esql`.** Each detection block can declare multiple formats; the Generator emits each as a separate file. Per §4.20: the blog's native format plus Sigma as a portable companion is the cumulative default when both make sense.

**Detection enums (component, severity, format) are closed in v1.** Extended only by maintainer PR, not by agent proposal at runtime. This is intentional: these enums reflect industry-stable categories, and runtime additions would create more noise than value. Agent proposals are reserved for the open-set registries (value_types, facets, execution_contexts, thesis_types — the last added by ADR 0045). Note `external_data_sources` is **not** runtime-proposable (maintainer PR only; see §4.16 — adding a source needs adapter code), despite older wording elsewhere.

**`cli_equivalent`** is an optional list of CLI commands that perform the same action as the step's programmatic implementation. Useful in docs ("to see what this step does manually, run...") and as a sanity check that the step's intent matches a known CLI invocation. **`cli_equivalent` is illustrative, not authoritative** — the docs can present it as "approximately equivalent" rather than "exactly equivalent." The semantic cross-check does not verify that the CLI command produces the same effect as the programmatic implementation; the field is for human readers, not validation.

**`tradecraft_notes`** — fine-grained "how" details about the attacker's choices within a step. Each note has an optional `name` (kebab-case, for cross-blog matching), a description, and optional `evades_what`. Tradecraft is not a registry — names are author-chosen and may collide. **Soft naming convention:** prefix names with the phase's primary `target:*` facet category, so `aws:anonymous-listing` and `github:anonymous-listing` aggregate cleanly without spurious collisions. The convention is soft (not enforced structurally) but the Extractor's prompt and the Docs Generator's prompt both apply it. Telemetry, when users submit, aggregates by prefixed name across runs to inform future schema decisions.

### 4.8 The AttackSpec envelope

The AttackSpec is a separate YAML file (`attack-spec.yaml`) produced before the manifest exists. The high-level shape:

```yaml
spec_version: 1
spec_kind: AttackSpec
source: <SourceBlock>
extraction_outcome: in_scope | out_of_scope   # see below
extraction_outcome_reason: <string>           # required when out_of_scope
thesis: <ThesisBlock>
facets: [<FacetReference>, ...]
external_references: <ExternalRefsBlock>
real_world_incidents: <RealWorldIncidentsBlock>
chain:
  chain_steps: [<ChainStepBlock>, ...]
  alternative_paths: [<AlternativePathBlock>, ...]  # captured in v1; generated v1.5+
defender_techniques: [<DefenderTechniqueBlock>, ...]
defenses: [<DefenseBlock>, ...]
reproducibility: <ReproducibilityBlock>       # lab-level, derived
gaps: [<GapEntry>, ...]                       # top-level enumeration; see pipeline.md §3.2.2
extraction_metadata: <ExtractionMetadataBlock>
extras: [<ExtrasBlock>, ...]
```

**`source`** — provenance of the blog. Fields: URL, canonical URL, title, publisher (with `kind`: `vendor_lab`, `researcher_personal`, `vendor_advisory`, `conference_writeup`, `other`), authors, publication date, fetched-at timestamp, content hash, fetch method, word count. The publisher `kind` is used downstream by the Docs Generator for framing ("this attack was disclosed by vendor X's research lab" vs. "documented by researcher Y").

**`extraction_outcome`** — top-level discriminator: `in_scope` (continue planning) or `out_of_scope` (halt with the out-of-scope notice per `pipeline.md §3.1.1`). The `extraction_outcome_reason` field is required when `out_of_scope` and has a quality floor: minimum 30 characters of substantive content referencing a specific reason (the blog category that put it out of scope, e.g., "pure on-prem AD attack with no cloud or supply-chain surface" or "post-incident commentary without exploitable chain"). Generic strings like "out of scope" or "no" fail the structural validation. The Extractor's quality bar (`agents.md §5.4`) requires substantive reasoning here so the user understands *why* the blog was rejected. Placing this discriminator at the top level (rather than buried in thesis or extras) lets the framework dispatch on it without parsing structured content.

**`thesis`** — multi-value, composable:

```yaml
thesis:
  types:                                       # multi-value, open-set with registry-evolution
    - vulnerability_chain
    - cross_tenant_compromise
    - cloud_provider_flaw
  summary: <provenance-wrapped string>
  attacker_objective: <provenance-wrapped string>
  vulnerability_story: <provenance-wrapped string>    # substantive for vulnerability blogs;
                                                       # may be empty for pure ttp_chain blogs
  duration_as_described: <provenance-wrapped string>
```

The `types` list has an initial v1 set seeded by the curated walk: `ttp_chain`, `vulnerability_chain`, `misconfiguration`, `cloud_provider_flaw`, `supply_chain_compromise`, `incident_analysis`, `cross_tenant_compromise`, `privilege_escalation`, `persistence_pattern`, `detection_methodology`. Grows by addition through registry-evolution (§4.16).

The `vulnerability_story` field is a structured place for the technical mechanics of the flaw being exploited. For TTP-chain blogs this is small or empty; for vulnerability-disclosure blogs it's the educational core. Distinct from `lab_resources` (provisioned world state) and from `chain` (attacker actions). The Docs Generator's `concepts.md` template uses this content when present.

**`facets`** — same shape as the Manifest's facets. The Extractor populates `target:*` and blog-derived `lab_class_signal:*` facets; runtime and lab-derived `lab_class_signal:*` facets are determined by the Planner based on what the lab will provision. See §4.13, §4.16.

**`external_references`** — CVEs cited (with provenance), related blogs, advisories, MITRE ATT&CK techniques cited.

**`real_world_incidents`** — explicit `status` field with three values:

```yaml
real_world_incidents:
  status: unknown | none_observed | incidents_documented
  evidence_source: <provenance-wrapped string>   # required when status != unknown
  incidents:                                       # populated when status == incidents_documented
    - incident_id: <generated stable ID>
      name: "Coinbase tj-actions targeting"
      description: <provenance-wrapped string>
      affected_organizations: [Coinbase]
      attribution: null                            # or threat actor name
      date_range: <provenance-wrapped string>
```

`none_observed` means "the blog or its vendor explicitly states that no real-world abuse was detected." Different from `unknown` (positive evidence of negative).

**Framework override of status.** Pre-Planner enrichment (`pipeline.md §3.2.4`) may override `unknown` with `incidents_documented` based on external API findings (e.g., security news APIs reporting active exploitation), following the framework-only-authorship rule for `source=external_api` (§4.9). Both citations (the blog's silence on incidents, and the API's reported incident) are present.

**`chain.chain_steps`** — ordered list of chain steps as the blog tells them. Each chain step:

```yaml
- id: chain-step-1
  step_number: 1
  title: "Submit malicious pull request"
  description: <provenance-wrapped string>
  blog_excerpt: |
    "The attacker submitted a malicious pull request to spotbugs/sonar-findbugs that exploited..."
  techniques:
    mitre: [T1195.002]
    tradecraft:
      - name: pull-request-target-abuse
        description: <provenance-wrapped string>
  preconditions:
    - <provenance-wrapped string>
  postconditions:
    - <provenance-wrapped string>
  detections: [<DetectionBlock>, ...]
  reproducibility:
    classification: full | partial_simulation | demonstration_only | not_reproducible
    caveats: <provenance-wrapped string>
    why: <provenance-wrapped string>                  # why this classification, not a higher one
  depends_on: [chain-step-0]                          # for DAG shape; most blogs sequential
  provisioning_mechanism: terraform | cloudformation | arm_template | gcp_deployment_manager | cli_scripts | manual | mixed
```

The chain in an AttackSpec is **always a list with optional `depends_on` per chain step**. Most blogs are sequential; the optional dependency declarations cover fan-out cases. DAG semantics emerge from the dependency graph; the schema doesn't require explicit DAG syntax.

**`chain.alternative_paths`** — captures alternative attack paths the blog presents. Each path is its own ordered list of chain steps, possibly sharing some steps with the canonical path:

```yaml
chain:
  chain_steps: [...]                                  # canonical path
  alternative_paths:
    - id: path-b-b2b-trust-hopping
      name: "B2B trust hopping"
      description: <provenance-wrapped string>
      chain_steps: [<ChainStepBlock>, ...]
      shares_steps_with_canonical: [chain-step-1, chain-step-2]
      reproducibility_summary: demonstration_only
```

**v1 captures alternative paths in the AttackSpec but generates only the canonical path.** v1.5+ supports user-selectable path generation (`architecture.md §8.2`). The Docs Generator surfaces alternative paths in `attack_narrative.md` as "the blog also describes these alternative paths, not generated in this lab," so users know the content was acknowledged but not implemented.

**`defender_techniques`** — present primarily for incident-analysis blogs:

```yaml
defender_techniques:
  - id: defender-technique-1
    name: "Trace shadow commits in deleted GitHub forks"
    description: <provenance-wrapped string>
    technique_kind: investigation | detection_engineering | threat_hunting | forensic_analysis
    applies_to_chain_steps: [chain-step-3, chain-step-4]
```

For non-incident-analysis blogs, this list is empty. Distinct from `defenses`: defender techniques are *investigation methodology*; defenses are *controls*.

**`defenses`** — controls that prevent or detect the attack:

```yaml
defenses:
  - id: defense-1
    description: <provenance-wrapped string>
    applicability: customer_actionable | architectural_mitigation | detection_only | vendor_only
    addresses_chain_steps: [chain-step-3]
    detection_path: detection/phase_3/kql.yml         # when applicability includes detection
    detection_format: kql
```

**`reproducibility`** — lab-level. **Derived from per-step `reproducibility` values**, not authored separately:

```yaml
reproducibility:
  classification_lab_level: full | partial_simulation | demonstration_only | not_reproducible | mixed
  caveats: [<string>, ...]
  overall_assessment: <provenance-wrapped string>   # optional; framework leaves null in v1, a later prose-producer authors it (ADR 0088)
  derivation_trace: [<string>, ...]    # which steps' tiers led to the classification
```

**Derivation rule.** When all required chain steps (required = not dropped to `not_reproducible`) share the same reproducibility tier, the lab takes that tier's classification (`full`, `partial_simulation`, or `demonstration_only`). When required chain steps span multiple tiers — regardless of proportions — the lab is classified `mixed`. When *no* required chain steps remain — every chain step was dropped to `not_reproducible` — the lab is classified `not_reproducible`; the framework only classifies, and the Planner turns an all-`not_reproducible` lab into a `cannot_plan` refusal (`agents.md §5.7`). The `caveats` field surfaces which tiers are present and in what proportions (e.g., "9 of 10 phases are full; 1 phase is demonstration_only because it involves a destructive payload that cannot be safely executed in a lab").

The any-heterogeneity-mixed rule is more honest than a weakest-tier rule. A lab with 9 `full` phases and 1 `demonstration_only` phase is qualitatively different from a fully-demonstration lab; calling both "demonstration_only" misleads the user. `mixed` plus caveats accurately describes what the user has.

The **framework** applies the rule mechanically — it is a deterministic rollup over the per-step tiers, not an LLM judgment (`architecture.md §1.5`) — and records the derivation in `derivation_trace`. The Docs Generator reads the classification and caveats to write README copy that matches what the user will actually experience.

**`extraction_metadata`** — extractor version, model, completeness score, list of unknown fields, citations count, notes for the Planner.

**`extras`** — top-level escape hatch.

### 4.9 Provenance metadata pattern

Every content field in both the AttackSpec and the manifest carries provenance metadata. This is a uniform pattern, not a per-field special case.

A field with provenance has the following shape:

```yaml
field:
  value: <the actual content>
  source: blog_explicit | external_api | llm_inference | unknown_from_blog | user_provided
  citations: [<CitationBlock>, ...]
  confidence: <float 0.0–1.0, required for llm_inference>
  confidence_source: framework_computed | model_self_reported   # required when confidence is set
  requires_user_confirmation: <bool>   # set by the framework; see below
  reason: <string>                     # required when source is unknown_from_blog
  # Set true by the framework on any field it writes during pre-Planner enrichment
  # (§3.2.4) — marks a framework-made authoritative API call, distinct from an
  # agent-claimed external_api field. Default false/absent.
  framework_enriched: <bool>
  # Set by the framework when pre-Planner enrichment overrides a blog value
  # with an authoritative external_api value. Preserves the audit trail.
  discrepancy_with_blog: <bool>
  overridden_blog_value: <T>           # required when discrepancy_with_blog is true
  discrepancy_classification: material | non_material   # required when discrepancy_with_blog is true
```

#### Sources

- **`blog_explicit`** — the value is directly stated in the source blog. Citation: blog passage references with section/paragraph identifiers. Confidence: 1.0 (default).
- **`external_api`** — the value comes from an external data source. Citation includes API name, endpoint, response field, fetched-at timestamp. The authoritativeness mapping (NVD is authoritative for CVE metadata; MITRE for technique definitions; etc.) is encoded in the `external_data_sources` registry — see §4.14.
- **`llm_inference`** — the value is inferred by the LLM from blog content. Citation includes blog passages used as evidence and a reasoning trace. Confidence required. **Confidence source.** Framework-computed where possible (from multi-call agreement, log-probability heuristics when the provider exposes them); model-self-reported as fallback with explicit weak-signal framing. The Critic and juries treat self-reported confidence as a weak signal; framework-computed confidence (from multi-call agreement, when affordable) is treated as stronger.
- **`unknown_from_blog`** — the value could not be determined. A free-form `reason` string is required (e.g., "blog only describes outcomes, not technique," "requires external research," "API rate-limited at enrichment time"). Free-form rather than enum: telemetry aggregates by string and informs schema-evolution decisions about whether to stabilize a reason taxonomy in a future version.
- **`user_provided`** — the value was supplied by the user during interactive mode.

#### `requires_user_confirmation` flag

The `requires_user_confirmation` flag is set by the **framework** (never by agents — consistent with the `architecture.md §1.5` invariant) on a per-field basis to flag the field for surfacing at the post-Extractor or post-Planner interrupt's per-field review. The flag is set when either of the following conditions hold:

- The field's `source` is `llm_inference` AND `confidence` is below the user-confirmation threshold (a `§8.4` placeholder pending eval calibration; suggested starting value 0.6).
- The field is structurally valid but the framework's enrichment or cross-check identifies it as decision-shaping for downstream stages AND uncertain (e.g., a CVE severity that was inferred from blog text rather than fetched, on a chain whose lab class depends on the severity tier).

Fields with `requires_user_confirmation: true` surface in the post-Extractor and post-Planner interrupts (`pipeline.md §3.2.5` and `§3.2.8`) as a per-field review surface, alongside the artifact-level review, the per-proposal review, and the material-discrepancy review. In `--auto` mode, fields with the flag set are listed in the run report but do not interrupt; auto-mode users have already accepted the trade-off between attention cost and review completeness.

The default is `false`. Agents do not set this field on their own outputs; the framework sets it after agent output passes static-schema validation, by inspecting source/confidence and the framework's decision-shaping heuristics. The semantic cross-check may also set the flag based on cross-block consistency findings (e.g., a phase that uses a value type only declared at low confidence).

#### Discriminator: structural vs. content fields

Structural fields (ids, names, file paths, type references, function names, step numbers, semver versions, timestamps) **do not** carry provenance — they are validated structurally. Content fields (descriptions, summaries, cli_equivalents, tradecraft_notes, expected_detections, real_world_incidents, severity claims, anything inferred or extracted) **all** carry provenance. When in doubt, treat as content and require provenance. This rule applies uniformly across AttackSpec and Manifest.

#### Framework-imposed sources

When pre-Planner enrichment finds a value that contradicts the Extractor's `blog_explicit` finding (e.g., blog says CVSS critical, NVD says CVSS medium), the framework — not an agent — sets the field's `value` to the API's authoritative value, sets `source: external_api`, and includes citations to *both* the blog passage and the API response. Agents never set this combination of source+citations themselves; this preserves the `architecture.md §1.5` invariant (LLMs do not decide control flow, including "should I override the blog with the API?").

**Every such override is recorded structurally on the provenance metadata** (`discrepancy_with_blog: true`, with the original blog value preserved in `overridden_blog_value` and the materiality classification recorded in `discrepancy_classification`). The materiality is determined by the source's `discrepancy_materiality_rules` declaration (§4.14). The Generator and docs use the API value going forward; the post-Extractor interrupt surfaces material discrepancies for user review (`pipeline.md §3.2.5`); the Critic notes both material and non-material discrepancies in its quality assessment.

Authoritativeness is per-source-per-field-type, not absolute. The authoritativeness mapping is encoded in the `external_data_sources` registry (§4.14). New sources extend the mapping via registry entry.

#### Framework-enriched vs. agent-claimed `external_api`

Both an agent-claimed external lookup and the framework's own enrichment call land as `source: external_api`, but they are grounded differently and the pipeline must tell them apart — so enrichment stamps every field it writes with **`framework_enriched: true`**:

- **`external_api` + `framework_enriched: true`** — the *framework's* own authoritative call during pre-Planner enrichment (`pipeline.md §3.2.4`). The API-response citation is the evidence; there is no agent tool-call to point at, and none is required.
- **`external_api` without `framework_enriched`** — an *agent-claimed* external value. The agent must have a matching tool-call in its trace (search-before-claim, `§4.15`); the mechanical provenance-structure layer (`validation.md §6.10.2`) rejects it otherwise.

This distinction is load-bearing because enrichment runs **before** the jury (`pipeline.md §3.2.4`, so what ships equals what was reviewed). Framework-written `external_api` fields therefore reach the jury and the mechanical layer; without the `framework_enriched` mark they would be false-flagged as ungrounded (no agent trace entry). The mark is what lets both **exempt** them while still holding *agent-claimed* `external_api` fields to the tool-backed requirement.

#### Two things called "unknown" — they're different

> **Callout: value-type unknown vs. content unknown**
>
> - **`__unknown__` value-type placeholder** — REMOVED from the architecture (`architecture.md §8.5`). For values that flow between phases at runtime, the system requires a real registry entry. The Extractor must propose a real entry when no existing one fits; there is no untyped fallback that produces a coherent lab.
>
> - **`unknown_from_blog` content provenance** — KEPT. For content fields (descriptions, cli_equivalents, severity claims, etc.) that the blog didn't address. The Generator falls back to safe defaults (omit the field, use a placeholder, or generate a "this information was not available in the source" stub in docs). This is different from a typed value: the field's *content* is missing, not its *type*.
>
> Don't conflate them. Value types must always be typed; content fields can be honestly marked unknown.

#### Code and content artifacts do not carry inline provenance

Code is generated by the per-phase Generator, the Lab-level Generator, the Cleanup Generator, and the Docs Generator. Each Generator emits a `.generator-trace.json` linking each generated function, resource, script, or doc section to the manifest element it implements and the reasoning that produced it. Each generator's trace is its own file (e.g., `.generator-trace.per-phase-3.json`, `.generator-trace.lab-level.json`, `.generator-trace.cleanup.json`, `.generator-trace.docs.json`), co-located under `.cyberlab-gen/` in the lab directory. These are separate audit channels from manifest provenance. Manifest provenance traces *what* was decided; generator traces trace *how* the artifact was assembled from those decisions.

The provenance pattern means the system's output is **auditable end-to-end**. Every value in a generated lab can be traced back to a blog passage, an API response, or an explicit LLM inference with reasoning.

#### Refinement addressing: field paths and patches

Refinement (`architecture.md §1.7`) edits the AttackSpec by **field path**, never by re-emitting the whole artifact. Every field is addressable by a dotted/indexed path (e.g. `chain[2].technique`, `cleanup.summary`). The structured findings that drive refinement carry that address: jury field-level feedback as `{field_path, problem, suggested_fix}`, and a mechanical static-schema finding as `{code, location, detail}` whose `location` is the same kind of path.

On a `revise`, the responsible agent returns a **patch** — new `{value, source, citations, …}` provenance subtrees for *only* the named field paths. The framework deep-sets the patch onto a copy of the prior AttackSpec and re-validates the result under the normal strictness rules (§4.17). Because only flagged paths are written, every unflagged field — value and provenance alike — is byte-identical to the prior spec; this is why refinement is **convergent by construction** (a patch cannot regress a field nobody flagged). Inline provenance survives a patch unchanged: a patched field carries the agent's new provenance, an untouched field keeps its original `source` and citations.

The patch is the default for both the Extractor (jury `revise`) and the Planner. Full from-scratch re-extraction is reserved for the artifact-level natural-language-feedback path in interactive mode, where the user rejects the artifact as a whole rather than naming fields.

### 4.10 The escape hatch: `extras`

A single mechanism handles content that doesn't fit the schema's structured fields.

**`extras`** is a free-form metadata block at four levels: lab, phase, step, and AttackSpec-level. It exists for information that doesn't fit the schema's structured fields but is worth preserving from the source blog. Each entry is `{name, description, source, citations}`. Example: a blog mentions historical context about Actor tokens being a legacy from SharePoint hybrid auth — there's no schema field for "historical context for the vulnerability primitive," but the information is educationally useful. It goes in `extras`.

The `extras` block exists primarily as a feedback signal for schema evolution. Non-empty `extras` flags labs that exercise patterns not yet first-class in the schema. Aggregated `extras` content over many labs becomes input to schema refinement.

**What `extras` is *not* for:** typed values that flow between phases at runtime. Those need a real `value_types` registry entry — either an existing one or a newly-proposed one (see §4.16). The schema does not provide a placeholder type for value-flow values; the Extractor must always type them.

#### Decision tree for typed values

- **Use existing type** when the type matches semantics and shape. Default; almost always the answer.
- **Propose new type** (via `propose_value_type` tool — see §4.16) when no existing entry fits and the type has a clear shape. The proposal goes through jury review, gets accepted (interactive) or auto-accepted (auto-mode) into the user overlay, and becomes part of the manifest immediately. There is no untyped fallback.
- **Use `extras`** when the content doesn't fit *any* typed slot — narrative quirks, historical context, references — not a value flowing between phases at all. Different category.

`extras` blocks are **measured by telemetry when the user submits**, and reviewed periodically. They are the system's feedback loop for schema gaps that aren't value-typed.

### 4.11 The registries

The system has **three first-class registries** that ship as YAML files in the cyberlab-gen distribution, **one reference-data registry** (static_catalogs, split from external_data_sources per §4.14), plus several **bundled-only catalogs**.

#### First-class registries (agents propose into; grow at runtime)

- **`registry/value_types.yaml`** — catalog of typed shapes for data values that flow between phases at runtime, are consumed as inputs, or are produced as outputs.
- **`registry/facets.yaml`** — catalog of declarations a lab can make about itself (target, runtime, lab_class_signal categories).
- **`registry/external_data_sources.yaml`** — catalog of authoritative external APIs that agents query for grounded data and the framework calls automatically during pre-Planner enrichment.

These are the registries the Extractor and Planner can propose into at runtime (per §4.16). They grow by addition.

#### Reference-data registry (consulted on demand; no runtime proposals)

- **`registry/static_catalogs.yaml`** — catalog of static reference data the Generator and Validator consult on demand for hallucination prevention (AWS IAM action catalog, Azure RBAC catalog, GCP IAM permissions catalog). Same entry shape as `external_data_sources` but no `enrichment_triggers` — these are consulted by `lookup_cloud_iam_action(cloud, action)` (per `agents.md §5.9`), not by automatic enrichment. Maintainer-curated; agents do not propose into it. The split from `external_data_sources` keeps the `enrichment_triggers` semantics clean (see §4.14).

#### Bundled-only catalogs (closed enums in v1; maintainer PR only)

- **Detection components, severity levels, detection formats** (see §4.7).
- **Provisioning mechanisms** (see §4.5).
- **Execution contexts** — open-set in spirit (Planner can propose), but new entries are rare; mostly maintainer-curated.
- **`registry/lab_credentials.yaml`** — canonical fake-credential patterns per platform (e.g., AWS `AKIAIOSFODNN7EXAMPLE`, GitHub `ghp_test_*` prefix). Read by the Generator (for planting fakes in lab content) and by the Validator's safety scans (for whitelisting). Maintainer-curated; agents do not propose into it. See `validation.md §6.8`.

v1.5+ may promote some of these to first-class registries if usage patterns warrant.

#### The bundled registry vs. the user overlay

There are two physical locations for first-class-registry entries:

1. **Bundled registry** — files inside the cyberlab-gen distribution at `registry/`. **Read-only at runtime.** Modified only via maintainer PRs and shipped via tool releases. This is the registry every user gets when they install the tool.

2. **User overlay** at `~/.cyberlab-gen/registry-overlay/` — **writable, local to this user.** A user-specific accumulation of additional registry entries the user has added through use (auto-accepted in `--auto` mode, user-accepted at the interactive interrupt).

At runtime the tool merges both: overlay entries take precedence when names collide with bundled entries. So a user always sees the union of "what shipped with the tool" plus "what I've added locally."

**Why the overlay exists.** When the Extractor encounters a value type that doesn't match any existing registry entry, it must propose one (§4.16) — there is no untyped fallback. The proposal needs to land *somewhere* available for this run and for future runs. The bundled registry is read-only at runtime (it's part of the tool's distribution; package managers and `git pull` assume it's immutable). The overlay solves this: the user accepts (or auto-accepts) a proposal, the entry goes into the overlay, the manifest uses the entry, the lab generates, and the entry is available for the next time the user runs against a blog that mentions the same value type.

**The overlay persists across runs.** A user who generates ten labs with cyberlab-gen accumulates an overlay reflecting the value types those ten labs encountered. The overlay is just a YAML directory the user owns; they can inspect, edit, or delete entries at any time.

**Promotion from overlay to bundled** happens via PR to the cyberlab-gen repo. If an entry is broadly useful — surfaced repeatedly across blogs in eval-harness data — a maintainer PRs it into the bundled registry and ships it in a release. Other users then get the entry as part of `git pull`, no longer needing to propose it locally. This is the long-term feedback loop from "this user's lab needed type X" to "type X ships with the tool" — manual, deliberate, and reviewed.

**There is no runtime registry fetching from a remote source.** Bundled comes from the install; overlay comes from local use. The system never reaches out to any registry service over the network at runtime.

### 4.12 The `value_types` registry

Each entry in the `value_types` registry has:

- **`name`** — registry key, snake_case (e.g., `aws_credentials`, `github_pat`).
- **`description`** — what this type represents.
- **`schema`** — JSON Schema definition of the value's shape.
- **`sensitive`** — bool. Whether instances should be marked `sensitive = true` in IaC outputs and treated as secrets at runtime.
- **`examples`** — list of example values.
- **`notes_for_generator`** — guidance for the Generator about known LLM failure modes for this type. Authored at proposal time and stays with the entry; agents do not modify it at runtime (`architecture.md §1.3`).
- **`cleanup_metadata`** — optional. Hints for the Cleanup Generator when this type appears in `produces_world_state`. Examples:
  - For `github_branch`: "Branches are deleted by `git push origin --delete <branch>` or GitHub API DELETE; if the branch has been deleted already (common in attack chains that delete-after-create), the deletion command may return 404 which is not an error for cleanup purposes."
  - For `aws_iam_user`: "An IAM user with attached policies must have its policies detached (or deleted if inline) before the user itself can be deleted. AccessKeys must be deleted first. The order: list access keys → delete each → list policies → detach managed / delete inline → delete user. The deletion is `aws iam delete-user --user-name <name>`."
- **`platforms`** — informational tag on the entry (`aws`, `azure`, `gcp`, `github`, etc.). v1 has no registry browser CLI; future tooling (`cyberlab-gen registry list --platform aws`, planned v1.5+) may use it.

The v1 registry covers the value types observed in the curated blog set, plus seed entries across AWS, Azure (including Entra ID), GCP, GitHub, npm, and generic types. The exact list lives in `registry-details.md` (planned). **The count is whatever the curated walk surfaces; there is no fixed target.** New entries are added by registry-evolution (§4.16).

**Coverage areas (illustrative, not exhaustive):**

- Cloud credentials (`aws_credentials`, `azure_token`, `gcp_token` — distinct types, different shapes; Entra-specific tokens like `entra_actor_token`, `entra_impersonation_token`).
- Cloud resource references (`aws_lambda_reference`, `aws_s3_bucket_reference`, `azure_keyvault_reference`, `gcp_secret_reference`, `gcp_cloud_run_revision_reference`, etc.).
- Identity references (`aws_iam_user_arn`, `aws_iam_role_arn`, `azure_managed_identity_id`, `entra_user_object`, `entra_service_principal`, `gcp_service_account_email`, `github_user_id`, `github_pat`, `npm_token`).
- File and content references (`disk_seeded_file`, `github_repo_reference`, `github_workflow_run_id`, `npm_package_reference`).
- Inventory types (lists of resources discovered during reconnaissance).
- Generic types (`sensitive_string`, `vm_public_ip`, `ssh_private_key`, `secret_value`).

**Modifiers (deferred to v1.5+, see `architecture.md §8.2`).** Some types have natural modifiers like `validity_window`, `single_use`, `revocable`, `auto_expires`. These will be added as optional fields on registry entries when a real lab can't generate without them. v1 omits them.

### 4.13 The `facets` registry

Facets are organized into three **categories**: `target`, `runtime`, `lab_class_signal`. Each category has its own scoping rules and proposal authority.

#### Categories

**`target:*`** — what the attack targets. Open-set. Examples: `target:aws`, `target:azure`, `target:gcp`, `target:entra_id`, `target:aws_iam`, `target:gke`, `target:eks`, `target:aks`, `target:github`, `target:github_actions`, `target:npm_registry`, `target:azure_devops`. Indicates *what attack surface* the lab teaches about. **Extractor proposes** (blog-derived).

**`runtime:*`** — what the lab provisions and runs against. **Open-set with `first_class` flag**:

- **First-class runtimes in v1**: `runtime:aws`, `runtime:azure`, `runtime:gcp`, `runtime:github`. Code paths exist for per-platform validator coverage (when the real-platform apply pass returns in v2), credential check conventions, and cleanup specifics. Each first-class runtime entry has `first_class: true`.
- **Best-effort runtimes**: Planner can propose new runtime entries at runtime via standard proposal flow (e.g., `runtime:cloudflare`, `runtime:vercel`, `runtime:local`). Proposed entries are `first_class: false` by default; labs ship best-effort with honest coverage flags.

Labs targeting multiple platforms simply declare multiple `runtime:*` facets (e.g., `runtime:aws` + `runtime:github`); there is no separate "multi" facet. The fact that a lab is multi-platform is derived from the count.

Promotion from best-effort to first-class is a maintainer-PR activity (requires code: real-platform apply verification logic when available in v2, per-platform credential check conventions, etc.); see `architecture.md §8.2`. **Planner proposes** (lab-derived).

**Note — `platform:*` is not a facet.** `platform:*` (e.g. `platform:kubernetes`, `platform:github`) is an *eval-coverage* label used only in the blog-set manifest and walk §14 for breadth counting (`eval.md §7.3`); it is not a facet, has no registry entry, and is proposed by no agent. For the attack surface under test, use `target:*`.

**`lab_class_signal:*`** — facets that influence lab shape. Examples: `lab_class_signal:incident_analysis`, `lab_class_signal:vulnerability_chain` (blog-derived); `lab_class_signal:simulated_components`, `lab_class_signal:multi_language`, `lab_class_signal:parameterized`, `lab_class_signal:requires_infra`, `lab_class_signal:produces_world_state`, `lab_class_signal:expected_detections`, `lab_class_signal:manual_prereq`, `lab_class_signal:external_channel` (mostly blog-derived or split). Two facets named in earlier drafts that need description:

- `lab_class_signal:time_marked` — the attack chain or detection depends on time-of-day, day-of-week, or timing windows being part of the scenario (e.g., the attack only works during business hours; the detection rule uses time-of-day correlation). Blog-derived.
- `lab_class_signal:waits_for_condition` — the lab requires the user to wait for an external condition before continuing (e.g., a scheduled scanner run, a registry propagation, a TTL expiry). Lab-derived; surfaced as a `mid_lab` prereq with a wait-and-confirm prompt.

**Authorship is split.** Blog-derived signals (the lab's "narrative" character — `incident_analysis`, `vulnerability_chain`, `external_channel`, `time_marked`) are proposed by the **Extractor**. Lab-derived signals (the lab's "implementation" character — `simulated_components`, `multi_language`, `parameterized`) are proposed by the **Planner**. The proposing authority for each subcategory is documented in the registry's entry-level metadata.

**Defender-mode and observer-mode labs are deferred to v1.5+** (`architecture.md §8.2`). The v1 schema deliberately does not reserve namespace for them; reintroducing the relevant facet category alongside its consuming code is a clean schema-version bump under the no-migration discipline (`architecture.md §0.6`). v1 has no code paths that read or write actor-perspective declarations, so the schema doesn't accommodate them.

#### Facet entry shape

```yaml
- name: target:entra_id
  category: target
  proposed_by: extractor              # extractor | planner
  description: "Microsoft Entra ID (formerly Azure AD), the identity tier"
  applies_at_levels: [lab, phase]
  requires_fields: []                 # additional fields required when this facet is declared
  implies: []                         # other facets that are automatically true
  incompatible_with: []               # facets that contradict this one
  examples: ["dirk-jan-entra-actor-tokens"]
  first_class: true                   # for runtime:* facets; absent or false otherwise
  notes_for_extractor: |
    Use this when the attack targets Entra ID specifically (not just Azure subscription resources).
```

**`implies` is consumed by the Validator's semantic cross-check** (`validation.md §6.5`): if the manifest declares `target:eks`, the Validator confirms that `target:aws` and `target:kubernetes` are also declared. Missing implied facets are *flagged as findings* — the Validator does not mutate the manifest. The refinement coordinator routes the finding to the Planner, which adds the missing facets in the next iteration (preserving the framework-only-authorship discipline; the Validator stays read-only).

**`incompatible_with`** declares facet pairings that contradict each other (e.g., `runtime:aws` is incompatible with hypothetical `target:on_prem_only`). The semantic cross-check enforces.

Facets do not encode behavior directly. They are declarations consumed by downstream agents and validators. The `lab_class_signal:manual_prereq` facet, for example, tells the Validator to expect a `prereqs` block; tells the Docs Generator to generate a "Prerequisites" section; tells the Lab-level Generator to emit prereq-checking logic in `setup.sh`. Facets are how the schema stays composable.

**The previously-discussed `produces_capabilities` and `requires_capabilities` facets are dropped from v1.** They were redundant with the `produces_world_state` block at phase level and the `lab_resources` block at lab level (`architecture.md §8.5`).

### 4.14 The `external_data_sources` and `static_catalogs` registries

Two related registries with the same entry shape but different semantic roles. Both ship at `<install-dir>/registry/` and may be overlaid by the user; the split is about *how the system uses each entry*.

**`external_data_sources`** — enrichment APIs that the system calls *automatically* during pre-Planner enrichment when the AttackSpec contains matching content. Each entry has meaningful `enrichment_triggers` declarations; the framework consults these triggers, not the agents directly.

**`static_catalogs`** — reference data for hallucination prevention. The Generator and Validator consult these *on demand* when they need to verify a claim (does this IAM action exist? does this Azure RBAC role have this permission?). Entries have no `enrichment_triggers` field; they're not called automatically. They're consulted via the `lookup_cloud_iam_action(cloud, action)` tool (per `agents.md §5.9`) and analogous catalog-lookup tools.

The split keeps `enrichment_triggers` semantics clean: an entry that's in `external_data_sources` has meaningful triggers; an entry that's in `static_catalogs` does not. Mixing them in one registry (which earlier drafts did) made `enrichment_triggers: null` a common artifact and obscured each registry's role.

#### Entry shape (both registries)

- **`id`** — registry key.
- **`name`** — human-readable name.
- **`description`** — what this source provides.
- **`base_url`** — API base URL (or static-file URL for catalogs).
- **`auth_type`** — `none`, `optional_api_key`, `required_api_key`, `oauth`.
- **`auth_env_var`** — environment variable for the API key, if applicable.
- **`rate_limit`** — declared limits: `{without_key: <rate>, with_key: <rate>}`.
- **`endpoints`** — list of operation definitions. Each has: id, method, path template, parameters, response schema reference, cache TTL.
- **`enrichment_triggers`** — *only in `external_data_sources`*. List of declarations for automatic pre-Planner enrichment. Example: `{field: "chain.chain_steps[*].cve_ids[*]", action: "lookup", endpoint: "lookup_cve"}`. Read by the framework at pre-Planner enrichment (`pipeline.md §3.2.4`); agents do not directly invoke enrichment triggers.
- **`discrepancy_materiality_rules`** — *only in `external_data_sources`*. Per-field rules for distinguishing material from non-material discrepancies between blog content and API findings (per `pipeline.md §3.2.4`). Material discrepancies surface at the post-Extractor interrupt; non-material ones are recorded in provenance.
- **`cache`** — `{ttl, scope}`. Scope is `per-key` (default) or `global`. Static catalog data (long TTL: weeks/months) and dynamic API responses (shorter TTLs: hours/days). No eviction strategy is specified for v1 — disk pressure for these caches is not a v1 concern.
- **`best_effort`** — optional boolean, default false. When true, the source is operationally fragile (e.g., RSS feeds that may go stale or change format) and the framework tolerates its unavailability without halting. Findings from `best_effort: true` sources are flagged as "may not be current" in the validation report.
- **`notes_for_extractor`** — guidance to the agent about when and how to use this source (in `external_data_sources`); for `static_catalogs`, `notes_for_generator` is the analog.

#### `external_data_sources` entries in v1

1. **NVD** — CVE lookups, CVSS scores, severity, affected products. No auth required (rate-limited); optional API key for higher rate limit. Authoritative for CVE metadata cross-cloud.
2. **MSRC** — Microsoft Security Response Center. Authoritative for Microsoft-issued CVEs (Azure, Entra, Microsoft 365). Used in addition to NVD when CVE is Microsoft-issued.
3. **MITRE ATT&CK** — STIX/TAXII technique data. Static JSON, no auth.
4. **OSV.dev** — cross-ecosystem vulnerability advisories (npm, PyPI, Cargo, Go, etc.). No auth.
5. **GitHub API** — repository metadata, file content, issue/PR data for blog-cited repos. Optional token for higher rate limit (60 → 5000 req/hr).
6. **AWS Security Bulletins** — AWS-side security disclosures via RSS. No auth. `best_effort: true`.
7. **Azure Security Advisories (MSRC feed)** — Azure-side security disclosures. No auth (within MSRC feed).
8. **GCP Security Bulletins** — GCP-side security disclosures via RSS / cloud.google.com. No auth. `best_effort: true`.
9. **CISA KEV** — Known Exploited Vulnerabilities catalog. Static JSON, no auth.
10. **EPSS** — Exploit Prediction Scoring System. No auth.

#### `static_catalogs` entries in v1

1. **AWS IAM catalog** — static JSON of all AWS IAM actions and resource type combinations. Used by Validator and Generator for hallucination prevention.
2. **Azure RBAC catalog** — static JSON of Azure built-in roles and actions. Used analogously to AWS IAM catalog for Azure-targeted labs.
3. **GCP IAM permissions catalog** — static JSON of GCP IAM permissions and predefined roles. Used analogously for GCP-targeted labs.

**API keys are never bundled** with the distribution. The system runs on no-auth tiers by default. Users may provide their own keys for NVD, GitHub, and others during interactive setup or in `~/.cyberlab-gen/config.yaml`. Documentation explicitly explains this is to avoid shared-key rate-limit collapse.

**Rate-limit during mandatory enrichment.** When a mandatory enrichment source is rate-limited mid-run, the framework records the skipped lookups with `unknown_from_blog.reason: "external API rate-limited at enrichment time"`. The lab still generates; the missing data shows up in provenance and may surface in Critic concerns or the validation report.

Adding new entries to either registry is registry-only in most cases; complex new sources (different auth model, different shape) may require code changes. Candidates explicitly deferred from v1: vendor-specific advisory feeds beyond the Big Three, language-ecosystem-specific sources beyond OSV (PyPI advisories, RubySec, etc.), threat intel feeds.

### 4.15 Agent access to external sources

Two patterns coexist:

**Mandatory pre-Planner enrichment** — When the AttackSpec contains specific fields that match an `enrichment_triggers` declaration in an external source, the framework calls that source automatically. The agent does not decide. Example: every CVE ID gets enriched with NVD data, KEV inclusion, EPSS score, and MSRC data (for Microsoft CVEs) before the AttackSpec is finalized. This is the "don't skip critical enrichment" guarantee.

**Agent-discretion lookups** — The agent has a generic tool `external_lookup(source_id, params)`, which takes a registry source id plus parameters and calls the API. Parameters are validated against the source's declared schema. The agent decides when to call. Example: the Extractor sees the blog mention a specific GitHub repository and decides to verify the repo exists by calling the GitHub API. This handles cases the framework can't anticipate.

**Agent-discretion calls are budgeted per stage** (v1 placeholder values, pending eval-harness calibration per `architecture.md §8.4`):

- Extractor: ~10 calls per blog.
- Critic: ~5 calls per lab.
- Validator: unlimited (it's the validation gauntlet; calls are mechanical).

Budget exhaustion is logged but not failure-inducing — the agent just can't call more.

**Per-run cap on framework-issued external calls.** Default 100, configurable. If the AttackSpec implies more lookups than the cap allows, the framework runs the highest-priority cap's worth and skips the rest with `unknown_from_blog` reasons indicating which calls were skipped. **Priority order** (when over cap): CVEs > MITRE techniques > GitHub repos > security bulletins > other authoritative sources.

**"Search-before-claim" pattern.** For any identifier with an authoritative source (CVE IDs via NVD, MITRE techniques via MITRE catalog, GitHub repos via GitHub API, npm packages via OSV or npm registry, etc.), the agent must look up before claiming; pure training-recall is rejected at the stage's structural validation. The provenance metadata records the search query and top-N matches as evidence.

For inferred CVE assignment specifically: the agent must execute a search call against NVD with extracted keywords, record the query and top results, and only then propose a CVE. Pure recall from training data is rejected.

### 4.16 Registry evolution

The three first-class registries are designed to evolve. Two evolution mechanisms:

**Manual evolution** — maintainers add or modify registry entries between releases. Standard PR workflow. Backward compatibility maintained: removing an entry that existing labs reference is a breaking change requiring a major version bump.

**LLM-proposed evolution** — agents propose new entries at runtime when they encounter values, facets, or sources that don't match any existing registry entry.

#### Proposal authority by registry

Four registries accept runtime proposals — `value_types`, `facets`, `execution_contexts`, and `thesis_types`. `external_data_sources` does **not** (a new source needs adapter code, not just a registry row).

- **`value_types`** — **Extractor only.** Blog-derived; the Extractor is the only agent that reads the blog.
- **`facets`** — **split by category**:
  - `target:*` and blog-derived `lab_class_signal:*` — Extractor proposes.
  - `runtime:*` and lab-derived `lab_class_signal:*` — Planner proposes.
- **`execution_contexts`** — **Planner.** When a step needs an execution context the registry doesn't yet name (the manifest would otherwise fall back to `other`), the Planner proposes a new context entry rather than letting `other` persist into the final manifest.
- **`thesis_types`** — **Extractor.** Blog-derived, like value types; no category gate. The Extractor proposes a thesis type when the blog's attack thesis matches no existing entry, so a spec naming a not-yet-registered thesis type ships (provisional pass → overlay-on-ship) instead of halting.
- **`external_data_sources`** — **no runtime proposals.** Adding a new source typically requires code changes (auth handling, response parsing). Maintainer PR only.

If the Planner finds itself needing a value type the Extractor didn't propose, that's a signal the Extractor missed something; the Planner-Jury flags this and the refinement loop routes back to the Extractor (see `pipeline.md §3.2.6`).

#### Proposal metadata

```yaml
proposed_entry:
  registry: value_types
  name: k8s_sa_token
  schema: { type: string, pattern: "^eyJ" }
  description: "Kubernetes service account JWT token"
  notes_for_generator: |
    Tokens at /var/run/secrets are base64-encoded with a specific claim
    structure; the lab should preserve the kid header which is significant
    for the attack.
  proposal_origin: llm_during_extraction
  source_lab: <lab-id>
  source_blog: <url>
  proposed_by_model: <model-id>          # framework-recorded, not agent-authored
  proposed_at: <timestamp>
  reasoning: "Blog describes harvesting JWT tokens from /var/run/secrets..."
```

(Notes: the `proposal_origin` field here is *not* the same as the content-field `source` from §4.9. It describes where the proposal came from in the pipeline. They share the word "origin/source" but operate on different objects. The `proposed_by_model` field is framework-recorded — the framework knows which model was picked from the capability-hint abstraction (`pipeline.md §3.5`) and records it. Agents do not have model self-awareness; they cannot author their own model id.)

#### Proposal lifecycle

The key discipline: **a proposal never mutates shared state until the spec that uses it ships.** Within-run resolution and global promotion are separate events with separate gates.

1. **Propose.** The Extractor (value types, blog-derived facets) or the Planner (runtime facets, lab-derived facets, execution contexts) emits a `proposed_entry`. The agent has already searched the bundled registry *and* the overlay and found no match; the proposal is the response to that absence, not a casual addition. (Which registries accept runtime proposals is set by "Proposal authority by registry" above; `external_data_sources` never does.)

2. **Provisional within-run resolution.** The proposed term resolves **within the current run only** — it is added to a run-scoped view of the registry so the AttackSpec can validate and the pipeline can proceed. **No global write happens here.** In `--interactive`, the user reviews each proposal at the post-Extractor / post-Planner interrupt (**Accept** or **Edit**; an edited proposal is re-validated against the registry entry schema, reopening the editor with errors as comments on a structurally invalid edit). In `--auto`, the proposal is accepted into the run-scoped view automatically. Rejecting a single proposal in isolation has no coherent semantics — the value exists in the spec and the system requires typed values; a user who disagrees Edits the proposal, gives artifact-level upstream feedback (option 2 of the menu in `pipeline.md §3.2.5` / `§3.2.8`) so the proposal changes when the agent re-runs, or Aborts.

3. **The jury reviews the spec — not the proposals in isolation.** The Extractor-Jury / Planner-Jury reviews the AttackSpec / manifest as a whole (fidelity, provenance, coherence). A proposal's *justification* is covered here implicitly: the field that uses the proposed term is reviewed like any other, and a term not justified by the blog surfaces as a provenance or fidelity problem on that field, fixed by the normal refinement patch (`§4.9`, `architecture.md §1.7`). There is no separate jury gate that judges proposals for overlap or shape — that is mechanical (stage 4). The jury does not propose its own entries.

4. **Promotion to the global overlay — gated on the spec shipping.** When (and only when) a run's spec **ships**, the framework writes that run's provisional terms to the shared `~/.cyberlab-gen/registry-overlay/`, applying a **mechanical merge-check at write time**: a proposed term that duplicates or overlaps an existing bundled/overlay entry is merged or dropped (dedup), not written twice. Each promoted entry is printed during the run (e.g., `[proposal promoted] k8s_sa_token added to overlay`) and listed in the run report. If the spec does **not** ship — jury `reject`, a mechanical halt, or a user abort — its provisional terms are **not** promoted; they remain only in the run record. This yields three structural guarantees:
   - A shipped spec's vocabulary is **always globally resolvable** — every term it references now exists in bundled or overlay, so a shipped lab can never carry a dangling reference to a term that exists in no registry.
   - There are **no orphan overlay entries** — a term reaches the shared overlay only alongside a spec that used it *and* shipped.
   - `--auto` promotes **only terms from specs that shipped**, which is the real guardrail. (The prior design auto-wrote *every* proposal to the global overlay with no gate — the structural root of the registry pollution and the "worked yesterday, broke today" cross-run nondeterminism.)

   The mechanical merge-check **replaces** the older "jury reviews proposals for overlap" gate: dedup/overlap is deterministic and auditable, consistent with the mechanical-safety-check rule (`architecture.md §1.6`), not an LLM judgment.

5. **Graduation to bundled.** From the overlay, broadly-useful entries graduate to the bundled registry via maintainer PR, informed by telemetry-aggregated usage across runs (`eval.md §7.9`). Human-curated and deliberate — unchanged by this lifecycle. Bundled ships to all users on `git pull`, so a graduated term no longer needs local proposal.

#### Overlay file shape: entries and proposals are separate

Bundled and overlay registry entries have **identical shape**. The overlay does not extend the entry shape with proposal metadata. Instead, the overlay YAML file has two top-level keys:

```yaml
# ~/.cyberlab-gen/registry-overlay/value_types.yaml
entries:
  - name: k8s_sa_token
    description: "Kubernetes service account JWT token"
    schema: { type: string, pattern: "^eyJ" }
    sensitive: true
    notes_for_generator: |
      Tokens at /var/run/secrets are base64-encoded with a specific claim
      structure; the lab should preserve the kid header which is significant
      for the attack.
    platforms: [kubernetes]
    proposed_by: extractor
    proposed_in_run: "run-20260514-abc123"

proposals:
  k8s_sa_token:
    proposal_origin: llm_during_extraction
    source_lab: <lab-id>
    source_blog: <url>
    proposed_by_model: <model-id>
    proposed_at: <timestamp>
    reasoning: "Blog describes harvesting JWT tokens from /var/run/secrets..."
```

The `entries:` block lists the registry entries themselves — same Pydantic shape as the bundled registry. The `proposals:` block is a dict keyed by entry name, with each value carrying the proposal envelope's metadata as audit context. The audit block is **not part of the registry-entry shape**; it lives alongside the entries in the overlay file only.

**Promotion to bundled.** When an entry is promoted via maintainer PR, the entry is copied to the bundled YAML and the corresponding `proposals:` block entry is dropped (the audit context is preserved in the cyberlab-gen repo's git history as the PR's commit message). Bundled registry files have only an `entries:` top-level key, not a `proposals:` block.

**Why separate, not on-entry.** Two reasons. First, bundled and overlay registry entries having identical shape simplifies the Pydantic model — no optional `proposal_audit` field block, no conditional logic about when the field is populated. The registry-entry Pydantic class is the same for both, and the registry-file-level model (`{entries:, proposals:}`) is what differs between bundled and overlay. Second, the proposal audit is about *acceptance history*, not about the entry's *content* — keeping it adjacent rather than inside avoids the implication that the audit is part of the entry's identity. Two runs that propose the same entry shape produce identical registry entries with different audit blocks; that's the right model.

#### Per-run cap on proposals

Default 5 per run (v1 placeholder, pending eval-harness data per `architecture.md §8.4`), configurable. The cap is enforced by **in-loop steering**, not a hard stop: when a run reaches the cap, the framework feeds the agent structured guidance to either

- (a) use an existing registry entry it might have missed, or
- (b) refactor the AttackSpec to eliminate the need for the additional types.

This steering is just another refinement signal, so it is **bounded by the refinement iteration and budget caps** (`architecture.md §1.7`): the agent gets bounded attempts to come back under the cap, and only if it cannot within those caps does the run terminate via the normal budget-exhaustion path (ship the best state with a `proposing-too-much` flag, or halt if no usable spec was produced). The cap is not a separate hard-halt mechanism that short-circuits the loop.

The cap protects the overlay from a single runaway run proposing redundant or hallucinated entries; combined with the spec-ships promotion gate (above), it does not produce broken or degraded labs.

#### Labs are decoupled from the registry after generation

Once a lab is generated:

- The lab directory contains only the manifest, code, IaC, scripts, and docs.
- The lab does not invoke cyberlab-gen at runtime. It does not load the registry at runtime. It does not depend on any overlay entry being present.
- The user can later remove an auto-accepted entry from `~/.cyberlab-gen/registry-overlay/` without affecting any lab that was generated using it. The lab still runs fine.
- The only consequence of removing an overlay entry is that **re-running `cyberlab-gen validate <lab-dir>`** (a rare operation, primarily for CI) against that lab will fail at static-schema validation because the manifest references a registry name no longer present. This is the same path as schema-version mismatch (`architecture.md §0.6`); the user gets a clear "regenerate from blog URL" or "restore the registry entry" message.

This decoupling is what makes auto-acceptance safe. The system is not committing the user to anything they can't undo — they can audit and prune the overlay at leisure without breaking labs they've already generated.

#### Why no `--auto-extend-registry` confirmation flag

Earlier drafts gated overlay extension behind an explicit user flag. The flag was theater. With labs decoupled from the registry after generation, there is no harm to auto-accepting a proposal: the entry can be reviewed and removed at the user's convenience, and removal does not break existing labs. The per-run cap handles runaway proposals without forcing the user to choose between fidelity (with flag) and degraded output (without). See `architecture.md §8.5`.

#### Empirical proposal discipline

The Extractor proposes a new type when (a) it's confident no existing entry fits (search the registry first, propose only on absence), (b) the type has a clear shape inferable from blog content, (c) the type doesn't substantially overlap with an existing entry. The jury's role is verifying these conditions held. If the agent proposes a type that's already covered, the jury flags and the agent re-runs using the existing entry.

### 4.17 Validation strictness

Manifest validation has two tiers:

**Structural validation (strict)** — required structural fields must be present and well-typed. Missing `id`, missing `phases`, malformed YAML, references to non-existent registry entries, type mismatches in `bind_inputs` — these are hard errors. Validator rejects the manifest. The `spec_kind` discriminator is enforced at this tier.

**Content validation (lenient)** — content fields can be empty (marked `unknown_from_blog`) or LLM-inferred (with provenance). The Critic measures completeness as a quality score but does not block. Some labs are inherently low-completeness because their source blogs are vague; rejecting them entirely loses coverage.

The two tiers exist because cyberlab-gen's value proposition is "generate labs from cloud-relevant blogs of varying completeness." Strict content validation would reject many plausible blogs at the extraction stage. Lenient content validation with provenance and quality scoring makes the system useful on imperfect input while keeping the auditability trail intact.

The categorical rule for tiering — strict for structural fields (ids, type references, function names, file paths), lenient for content fields with provenance metadata — lives in this section. Field-by-field tiering (which specific fields are strict vs. lenient, which are required vs. optional, which have minimum-content quality floors) lives in the planned companion `schema-details.md`. Adding a new required structural field is a breaking schema change. Adding a new lenient content field is backward-compatible.

### 4.18 What this section did not specify

Deliberately out of scope for §4, deferred to companion docs or later sections:

- Exact field-by-field YAML schema with every nullable, every constraint (planned companion: `schema-details.md`).
- The full v1 entries in each registry (planned companion: `registry-details.md`).
- Agent prompts that consume the schema (covered in `agents.md`).
- Validator's exact rule set per layer (covered in `validation.md`).
- Eval metrics over manifest properties (covered in `eval.md`).
- A registry browser CLI / web UI (deferred to v1.5+, per `architecture.md §8.2`).

### 4.19 Section summary

The lab manifest and AttackSpec are two distinct structured artifacts with distinct roles: AttackSpec mirrors the blog narrative (chain_steps), manifest describes the implementation (phases with steps inside). Both are versioned, both use the same provenance metadata pattern, both have escape hatches for unmodeled patterns.

The system's runtime-proposable vocabulary registries — `value_types`, `facets`, `thesis_types`, `execution_contexts` — are bundled with the distribution and evolvable through PR workflow or LLM-proposed entries. `external_data_sources` is a separate catalog of tool adapters (queried at runtime / pre-Planner enrichment), evolved by maintainer PR only — adding a source needs adapter code, not just a registry row (§4.14, ADR 0055/0058). Bundled-only catalogs (detection components, severity, formats, lab credentials) are closed in v1 and maintainer-curated. Validation is strict on structure, lenient on content with completeness as a quality signal.

Provenance is uniform: every content field carries source, citations, and confidence. The framework — not agents — authors enrichment-driven content (`source: external_api`), enforcing the §1.5 invariant that LLMs don't decide control flow.

The schema accommodates emergent lab class (`architecture.md §0.7`) by encoding per-step decisions and reproducibility classifications, not by enumerating lab classes upfront. The Planner's grouping of chain steps into phases, the choice of `step_composition` per phase, the `execution_context` per phase, the `provisioning_mechanism` per phase or resource, the per-step `reproducibility` — these combine to produce a lab whose character emerges, rather than being declared.

### 4.20 Preference ordering and choice discipline

This subsection consolidates the preference-ordering and choice-discipline rules referenced throughout the architecture. Three categories of choices:

#### Fallback ladders (true preference orderings)

**Reproducibility (per chain step):**

1. `full` — attack reproduces against real, lab-provisioned resources end-to-end.
2. `partial_simulation` — attack reproduces against real, lab-provisioned resources, with some component mocked or substituted (e.g., local Verdaccio for npm registry; self-hosted GitLab for GitHub-specific test).
3. `demonstration_only` — no attack runs against real or simulated resources; the step is documented and possibly accompanied by a script that *prints* what would happen. Demonstration must be **meaningful**: a script printing "the attacker would now exfiltrate the data" without showing structured data shape, real API call patterns, or genuine technique mechanics is worse than nothing. The bar: the user learns something they couldn't learn from reading the blog.
4. *Drop the step* (`not_reproducible`) — when even a meaningful demonstration is not possible, the step is excluded from the lab and noted in `extras`.

**Type registry use (per typed value):**

1. Use existing `value_types` registry entry that matches semantics and shape.
2. Propose a new entry via `propose_value_type` (only when no existing entry fits; the proposal goes through review per §4.16).

There is no third fallback. Untyped values are not allowed in the manifest; the previously-defined `__unknown__` placeholder was removed because it shipped degraded labs without integrity guarantees (`architecture.md §8.5`).

**Provisioning mechanism (per phase or lab resource):**

1. Terraform — preferred for cross-cloud consistency and the broadest validator support.
2. Cloud-native IaC (CloudFormation / ARM template / GCP Deployment Manager) — when Terraform doesn't support a needed resource.
3. CLI scripts (aws CLI, az CLI, gcloud, gh CLI) — when no IaC supports it.
4. Manual prereq with check command — last resort, declared with `timing: pre_lab` or `mid_lab`.

**Real platform vs. local simulation (per lab):**

1. Real platform (the runtime declared in `runtime:*` facets) — preferred when attack mechanics depend on platform-specific behaviors (fork-shadow-commit on real GitHub; IMDS-based credential theft on real cloud; identity-tier behaviors specific to Entra ID).
2. Local simulation (Verdaccio for npm, Gitea/Forgejo for GitHub-as-a-VCS, MinIO for S3, etc.) — fallback when mechanics don't depend on platform specifics, or when the user can't (or won't) provide platform credentials.

#### Categorical choices (pick by content fit, not preference)

**Provenance source (per content field).** Each content field has *one* source, chosen by what produced the value, not by preference:

- `blog_explicit` — the value is directly stated in the source blog.
- `external_api` — the value comes from an external data source (NVD, MITRE, etc.). When the framework finds the API contradicts a `blog_explicit` Extractor finding, the framework rewrites the field with `source: external_api` and citations to both the blog passage and the API response (§4.9). This framework-imposed authorship is the most authoritative state a field can be in.
- `llm_inference` — the value is inferred by the LLM from blog content; used only when filling a known schema field and the blog implies (rather than states) the answer.
- `unknown_from_blog` — the value could not be determined; the schema permits this with a `reason` string.
- `user_provided` — the value was supplied by the user during interactive mode.

The discipline: **never silently fabricate**. Inference is allowed but must be marked and cited. If the agent cannot determine a value from blog content or external sources, the honest answer is `unknown_from_blog`, not an invented value with a confident-looking source.

**Defenses applicability (per defense).** A taxonomic categorization, not a preference order:

- `customer_actionable` — the customer can implement it themselves.
- `architectural_mitigation` — transferable design wisdom, broader than this lab.
- `detection_only` — reactive control surfacing alongside detection rules.
- `vendor_only` — only the vendor can fix; surfaced as factual context.

These don't degrade into each other. A defense is what it is.

#### Cumulative selections (combine, don't substitute)

**Detection format (per detection rule):** the agent emits the blog's native format *plus* Sigma as a portable companion. Both are written; the rule has two files.

**Boundary cases:**

- **Blog uses Sigma natively.** No companion file — the native and the companion would be identical. Single Sigma file.
- **Blog uses KQL, SPL, or ESQL (non-Sigma native format).** Emit Sigma as a companion in addition to the native file. Two files.
- **Blog doesn't specify a target SIEM format.** Sigma-only — Sigma is the answer when no native format is declared.
- **Native format can't cleanly express the detection** (e.g., the detection requires SIEM-specific lookups Sigma doesn't model). Native-only is acceptable; the detection block records why Sigma was omitted.

This isn't a fallback ladder — the native-plus-Sigma combination is the goal when both are achievable. Documenting it as a fallback ("prefer native, fall back to Sigma") was incorrect; the discipline is "emit both when both add value."

#### What the Critic verifies

For every fallback decision, the agent's reasoning trace records *which* option was chosen and *why* a higher option wasn't taken. The Critic uses these traces to assess whether the decisions were honest (e.g., did the agent give up on `full` reproducibility too easily, when the cost of `full` would have been only marginally higher?).

For categorical and cumulative choices, the Critic verifies content fit rather than preference: does the chosen provenance source match what actually produced the value? Does the defense applicability match the defense's nature? Was Sigma emitted alongside the native format where appropriate?

This discipline is what makes lab class **emergent** (per `architecture.md §0.7`) rather than pre-classified. Each chain step's reproducibility is decided independently; each value's provenance reflects how it was actually produced; the lab's overall character is the sum of those decisions.

---

*End of schema document. See `pipeline.md` for how stages consume and produce these artifacts, `agents.md` for component contracts, and `validation.md` for how validator layers check schema conformance.*
