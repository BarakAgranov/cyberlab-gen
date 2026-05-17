# cyberlab-gen — Validation Layers

**Companion to:** `architecture.md` (hub).
**Document scope:** The Validator's mechanical layers — what each layer checks, what it can and cannot catch, how its failures route into the refinement loop, what the report shape looks like, and what the validator deliberately does not do.

In v1, the Validator runs four layers (1, 2, 3, 5). Layer 4 (real-platform apply) is **deferred to v2** with rationale in `architecture.md §8.1`. The Layer 4 section is preserved here for design continuity; it is not active code in v1.

The Critic is a peer LLM-based assessment running alongside (not as a layer within) the Validator. The Critic's contract lives in `agents.md §5.14`; this document references it.

---

## 6. Validation Layers

### 6.1 What this section covers

`architecture.md §1.6` locked the decision that the validator runs mechanical layers, with the Critic as a peer LLM-based assessment running alongside (not as a layer within it). This section specifies what each Validator layer does, what it inputs and outputs, what it can and cannot catch, when it runs, and how its failures route into the refinement loop.

The Validator is **framework code, not an agent**. It runs deterministic checks. Some checks invoke external tools (terraform, tflint, container runtimes, per-cloud catalogs); none invoke LLMs. The Critic (`agents.md §5.14`) is the only LLM-based assessment of a complete lab; it runs *after* the Validator and consumes its report.

### 6.2 Why multiple layers

The layers exist because no single check catches everything, and different checks have different costs. Cheap checks run first, expensive ones run only after cheap ones pass. This minimizes the cost of the refinement loop: most refinement iterations resolve at Layer 1 or 2 without needing the expensive layers to run.

The layers also have different failure-routing semantics. A Layer 1 schema error means "the manifest is malformed" — re-run Planner or earlier. A Layer 3 dry-run failure means "the code has a runtime issue" — re-run per-phase Generator. Conflating these routes makes the loop dumber than it needs to be.

### 6.3 The five mechanical layers

The Validator schema defines five mechanical layers. In v1, four run; one is deferred:

1. **Static schema validation** — manifest and AttackSpec conform to schemas; required fields present; types match registries; `spec_kind` discriminator matches the loading point.
2. **Semantic cross-check** — declarations across artifacts are mutually consistent (e.g., manifest claims phase 3 produces an admin_session; phase 3's code actually sets it).
3. **Containerized dry-run** — IaC plans succeed; Python imports cleanly; static analyzers pass; payload files parse.
4. **Real platform apply** — *v2-deferred*. IaC actually applies in user's lab account/subscription/project; attack scripts actually run. See §6.7 for the v2 design.
5. **Safety scans** — generated artifacts checked for accidental dangerous content (real credentials, malware signatures, host-system attack patterns), with whitelisting against the canonical lab-credentials catalog.

**In v1, Layers 1, 2, 3, and 5 always run.** Layer 4 is deferred to v2; its slot in the validator report is `skipped: v2-deferred`. The Layer 4 numbering is preserved (not renumbered to 4) so the report structure stays stable across v1 and v2 — when v2 adds Layer 4, no other numbers shift.

The Critic runs as a peer stage to the Validator, not as one of these layers. Its output and the Validator's report together feed the refinement coordinator.

**Layered safety model.** The system's safety posture is layered, not single-mechanism (per `architecture.md §1.6` honest framing):

- **Scope** (`architecture.md §0.2`) is the primary defense. The system is shaped for cloud-relevant educational labs; it is not a malware sandbox or phishing kit generator.
- **Ingestion notices** (`pipeline.md §3.1.1`) inform the user when input warrants special care (recent CVE without public PoC). Not gates.
- **Layer 5 (safety scans)** catches accidental dangerous content (real credentials accidentally embedded, host-attack patterns). Best-effort pattern matching with whitelist for canonical fakes.
- **The Critic** catches blog-fidelity drift and over-engineering. Non-blocking; advisory.

No single mechanism prevents a determined adversary from misusing the tool. The combined model catches accidents and provides defense in depth for the well-intentioned user.

### 6.4 Layer 1: Static schema validation

**What.** Validates the manifest and AttackSpec YAML against their JSON Schemas. Validates that every reference into a registry (value_types, facets, external_data_sources) resolves to an existing entry. Validates that registry-cross-references inside the manifest (e.g., `bind_inputs` types match phase output types) are consistent. Validates that `spec_kind` matches the expected type at the loading point: loading an AttackSpec where a Manifest is expected (or vice versa) fails loudly with structural error.

**Inputs.** The manifest, the AttackSpec, the registry files (bundled + overlay merge).

**Output.** Pass/fail with a list of violations, each with file path + line number + violation description.

**Tools.** A JSON Schema validator. No LLM, no external network calls.

**Cost.** Milliseconds. Always cheap.

**What it catches.**

- Malformed YAML.
- Missing required fields.
- Wrong types (string where object expected).
- References to non-existent registry entries (`type: aws_credentialss` typo).
- Inconsistent type references (phase A declares output type X; phase B's `bind_inputs` expects type Y).
- Schema version mismatches → triggers the `architecture.md §0.6` "regenerate from blog URL" message.
- `spec_kind` mismatches at load points.

**What it cannot catch.**

- Semantic incoherence within valid structure (manifest says phase 2 produces an admin session, but the implementation doesn't actually set one).
- Code bugs.
- Anything outside the YAML.

**Failure routing.** Schema violations route back to whichever agent produced the malformed artifact:

- Manifest violations → Planner (or per-phase Generator if it's a phase block).
- AttackSpec violations → Extractor.
- Registry-reference errors in proposed entries → the proposing agent (per `agents.md §5.18`: value_types are always proposed by the Extractor; facets by Extractor for target/blog-derived categories or by Planner for runtime/lab-derived categories).

**Registry-reference errors specifically distinguish two cases:**

- The reference itself is wrong (typo, agent error) → route to proposing agent for re-run.
- The reference is correct but the overlay entry was deleted by the user since generation → user-facing message: "This lab references registry entry X which is no longer present in your overlay. Either restore the entry or re-run `cyberlab-gen generate` to regenerate with current registry state."

**Notes.**

- Layer 1 is the **single most important** validation layer because it's both cheap and high-coverage. Most generation errors surface here.
- The schema files are versioned with the codebase. A schema change is a release event.

### 6.5 Layer 2: Semantic cross-check

**What.** Verifies that declarations in one artifact match implementations in another. The manifest declares the system's "intended structure"; the generated code is supposed to implement that structure. Layer 2 checks correspondence.

**Inputs.** The manifest plus all generated code, IaC, payloads, scripts.

**Output.** Pass/fail with a list of mismatches, each describing the declaration and the divergence.

**Tools.** AST parsers (Python ast, HCL parser, ARM template parser, CloudFormation parser), file-existence checks, regex matching for shell scripts. No LLM.

**Cost.** Seconds. Cheap.

**What it catches.**

- Manifest declares `step.function_name = discover_bucket` but the phase module has no function by that name.
- Manifest declares phase 3 outputs `admin_credentials` but the phase code never returns that key.
- Manifest declares `produces_world_state: [{type: aws_iam_user, name: backdoor-admin}]` but cleanup.sh has no command targeting `backdoor-admin`.
- Manifest declares `lab_resources: [public_s3_bucket]` but IaC has no `aws_s3_bucket` resource with public access enabled.
- Phase declares input type `aws_credentials` but the phase code uses the value as a plain string without unpacking the credential structure.
- Manifest declares multiple `target:*` facets (e.g., `target:aws` + `target:github`) but only one provider block exists in IaC.
- **`references_lab_outputs` cross-check, both directions:**
  - Per-phase IaC references a `references_lab_outputs` entry that doesn't exist in the lab-level IaC's outputs (the Lab-level Generator failed to produce a declared output).
  - Per-phase IaC references a `lab_resources` entry that the Planner didn't declare in the manifest (the per-phase Generator misread the manifest and referenced a non-existent resource).
  Both directions need to be cross-checked. The first catches Lab-level Generator failures; the second catches per-phase Generator failures.
- **`produces_world_state` identifier_source resolution** (per `schema.md §4.5`): for every `produces_world_state` entry with `identifier_kind: runtime_generated`, Layer 2 verifies that the `identifier_source` path resolves to a declared phase output. A path like `phase_outputs.malicious_branch_name` must correspond to a key in the phase's declared `outputs` block. Without this check, cleanup code would compile but fail at runtime because the source it reads from doesn't exist.
- Facet `implies` relationships are enforced: if the manifest declares `target:eks`, Layer 2 confirms that `target:aws` and `target:kubernetes` are also declared. **Missing implied facets are flagged as findings; the Validator does not mutate the manifest.** The refinement coordinator routes the finding to the Planner, which adds the missing facets in the next iteration. This preserves the framework-only-authorship discipline — the Validator stays read-only and never authors manifest content.
- Facet `incompatible_with` relationships are enforced: contradictory facet pairings are flagged.
- **`affected_platforms` consistency** (per `schema.md §4.4`): if the manifest's core block has an `affected_platforms` field (where present in user-edited manifests), Layer 2 verifies it matches what's derivable from `target:*` facets. The facets are authoritative; an inconsistent `affected_platforms` field is flagged as a finding rather than treated as a different signal.

**Non-first-class runtime warning.** When the manifest declares a non-first-class runtime (per `schema.md §4.13`), Layer 2 emits a warning (not a failure) noting reduced coverage for that runtime. The warning appears in the report alongside the lab's per-phase confidence flags.

**What it cannot catch.**

- Logic bugs within correct structure (function exists with the right name but does the wrong thing).
- Subtle type misuse (passes type-check but wrong field accessed).
- Anything that requires understanding execution semantics rather than declarations.
- Semantic correctness against attack semantics (e.g., manifest declares `aws_credentials` and code returns `aws_credentials` of the wrong sub-type for the attack). That's the Critic's job per `agents.md §5.14`.

**Failure routing.** Mismatches route to the agent responsible for the *implementation*, not the manifest:

- Phase code missing a declared function → per-phase Generator for that phase.
- Per-phase `cleanup.sh` missing a world-state cleanup the phase owns → per-phase Generator for that phase.
- Lab-level `cleanup.sh` missing cross-phase shared state cleanup or wrong-order orchestration → Cleanup Generator.
- IaC missing a declared resource → Lab-level Generator (or per-phase if it's phase IaC).
- Docs reference non-existent step → Docs Generator.

When a mismatch is consistent across multiple artifacts (e.g., the manifest declares something and *no* artifact implements it), the routing prefers earlier-stage agents (Planner) on the assumption that the declaration itself is wrong, not that every implementer ignored it.

**Notes.**

- Layer 2 is where the manifest's role as "single source of truth" is enforced. Without Layer 2, the manifest could become aspirational metadata that doesn't match reality.
- Specific cross-check rules are encoded in the validator implementation; new cross-checks are added by extending the validator code as the system matures.

### 6.6 Layer 3: Containerized dry-run

**What.** Runs the lab's setup, attack, and cleanup logic in a container without applying IaC to any real cloud or platform. Catches issues that only surface when code actually executes — import errors, syntax errors, IaC plan failures, static analysis violations.

**Inputs.** The complete lab directory.

**Output.** Pass/fail with detailed logs from each sub-step.

**Tools.**

- A container runtime (Docker or Podman) on the user's machine.
- The cyberlab-gen base image (ships with Terraform, AWS CLI, Azure CLI, gcloud, gh CLI, Python, common tools).
- Static analyzers split into two categories with different strictness levels (see "Intentional misconfiguration" below):
  - **Code-quality analyzers** — ruff, mypy (Python); cfn-lint structural rules (CloudFormation); shellcheck (Bash). Run at conventional strictness.
  - **Security-finding analyzers** — tflint with per-cloud rulesets (`tflint-ruleset-aws`, `tflint-ruleset-azurerm`, `tflint-ruleset-google`), tfsec, checkov, cfn-lint security rules. The per-cloud tflint plugins matter because they catch cloud-specific resource misconfigurations that the generic terraform validator misses (an IAM policy that grants `*` actions, an S3 bucket with public read, an RDS instance with no encryption). Run at minimal strictness with intent-aware whitelisting.
- `terraform plan` (no apply); `aws cloudformation validate-template`; `az deployment validate` (or `az bicep build` for Bicep templates) for Azure ARM; `gcloud deployment-manager deployments create --preview` for GCP. Dry-run only; nothing applies.

**Container image footprint.** The base image ships all three cloud SDKs + IaC tooling + security scanners and is multi-GB. Users on slow connections or behind corporate proxies may experience friction at first pull. Image sizing, offline-degradation strategies, and possible split-by-runtime images (activated by the lab's declared `runtime:*` facets) are operational concerns documented in deployment docs (planned).

**Intentional misconfiguration (the dominant case).** For a lab generator, intentionally insecure resources are the dominant case, not an edge case. A lab teaching "exploit a public S3 bucket" requires generating a public S3 bucket; tfsec will fire on it on every run. The Validator handles this through two mechanisms:

- **Split strictness by dimension.** Code-quality rules run at conventional strictness (linting, type-checking, syntax must pass). Security-finding rules run at *minimal* strictness — informational rather than failing — when the resource is declared with `attack_target` in its `lab_role` per `schema.md §4.4`.
- **Intent declared in the manifest.** Layer 3 reads each `lab_resources` entry's `lab_role` list. When `attack_target` appears in the list, security-finding analyzers run against that resource but their findings are recorded as informational (`severity: informational_intentional_misconfig`) rather than failing the layer. Findings on resources *without* `attack_target` in their roles are real signals — they fail Layer 3 at the configured severity floor.

The mechanism preserves the architectural property that intent lives in the schema, not in validator heuristics. Layer 3 doesn't guess; it reads the manifest's declaration.

**Cost.** Tens of seconds to a few minutes per lab. **Most expensive always-on layer in v1** (Layer 4 is deferred to v2). The refinement loop tries to resolve issues at Layers 1 and 2 first to avoid Layer 3 round-trips.

**What it catches.**

- Python import failures (typos in module names, missing dependencies, circular imports).
- Python syntax errors that ruff or mypy detect.
- Type errors flagged by mypy at lab-conventional strictness.
- IaC configuration errors (`terraform init` and `terraform plan` failures, or equivalent for other mechanisms).
- IaC security findings from tfsec/checkov/cfn-lint above a configured severity floor (intent-aware per the split above).
- Cloud-API hallucinations cross-checked against the `static_catalogs` registry (per `schema.md §4.11`, §4.14) — e.g., a Terraform resource referencing an AWS IAM action that doesn't exist; an Azure RBAC role that doesn't exist; a GCP IAM permission that doesn't exist. Layer 3 consults static_catalogs on demand for each catalog-relevant identifier in the generated code; mismatches fail the layer with a specific "action X is not in the AWS IAM catalog" finding.
- Shell script syntax errors and shellcheck warnings above a severity floor.
- Missing Python packages (declared but not installable).
- Setup script failing on its read-only checks (e.g., references an IaC output that doesn't exist).

**Per-step reproducibility handling.** Layer 3 respects per-step reproducibility (per `schema.md §4.20`):

- `full` and `partial_simulation` steps undergo dry-run (terraform plan + python imports + script syntax).
- `demonstration_only` steps get **syntax validation only** — the demonstration script is intentionally non-functional; Layer 3 verifies it parses but doesn't try to "run" the demonstration.
- `not_reproducible` steps have no code to check; skipped.

**What it cannot catch.**

- Runtime errors that only surface when cloud/platform APIs respond with real data.
- Issues that depend on real cloud state (e.g., a step that fails because an EC2 instance hasn't finished booting).
- Race conditions, timeouts under real network latency.

**Failure routing.**

- Python errors → per-phase Generator (for phase code) or Lab-level Generator (for orchestration code).
- IaC errors → Lab-level Generator or per-phase Generator depending on which file.
- Security-scanner findings above floor → per-phase or Lab-level Generator with the specific finding.
- Shellcheck findings → Lab-level Generator (setup.sh) or Cleanup Generator (cleanup.sh).

**Notes.**

- The container image is pinned and versioned alongside cyberlab-gen. Image updates are release events.
- The intent-aware strictness split (code-quality vs. security-finding rules) is the architectural commitment; the exact rule sets and severity floors are encoded in the validator implementation and documented in the planned `validator-rules.md` companion.

### 6.7 Layer 4: Real platform apply (v2-deferred)

**Status: v2-deferred from v1.** Layer 4 is not active in v1. The rationale: automated real-platform apply during generation has asymmetric risk — when cleanup is broken (for hallucinated resources, missing permissions, race conditions, etc.), Layer 4 leaves orphaned cloud resources the user pays for. The system should not modify the user's real cloud without the user actively in the loop. The `cyberlab-gen fix` mode (`pipeline.md §3.4`) is the v1 mechanism for handling runtime issues with the user reviewing each patch. Layer 4 may return in v2 with stricter safety boundaries — mandatory pre-apply user confirmation, mandatory post-apply verification before any cleanup, refusal to apply when cleanup is structurally incomplete. The specification below describes the v2 design; **it is not active code in v1**.

**In v1, every shipped lab carries the `validated_static_only` flag** in the validation report since Layer 4 is deferred. Users who want real-platform validation rely on running the lab themselves (with `fix` mode assistance if needed).

---

#### V2 design (preserved for continuity)

**What.** Runs the lab end-to-end against the user's real platform credentials. For cloud platforms (AWS / Azure / GCP), this means actual Terraform/CloudFormation/ARM apply; running the attack script; running cleanup. For non-cloud platforms (GitHub, etc.), the equivalent — create real ephemeral repositories, run workflows, clean up. Confirms the lab actually works end-to-end against real systems.

This generalizes from "real cloud apply" to "real platform apply" because some labs target platforms that aren't traditional clouds (GitHub-centric labs, npm-supply-chain labs with real or self-hosted registries).

**Inputs.** The complete lab directory plus the user's credentials for whatever platforms the lab declares.

**Output.** Pass/fail with logs from setup, attack execution, and cleanup. Plus optional artifacts: the final state of declared world-state items (verified to exist before cleanup, verified gone after).

**Tools.** Whatever the lab's `provisioning_mechanism` declares — Terraform, CloudFormation, ARM, GCP Deployment Manager, gh CLI, etc. — plus the lab's own scripts. `verify.sh` runs as part of Layer 4 validation, executing the manifest-derived check list.

**Cost.** Minutes to hours per lab. Real cloud/platform costs (typically <$5 per run for simple cloud labs; multi-cloud labs higher; GitHub-centric labs effectively free on free tier).

**What it would catch.**

- Real-world issues that only surface in actual platform state.
- IAM/RBAC policies that don't grant the permissions the attack assumes.
- Service quotas, region availability issues.
- Resources that aren't actually publicly accessible despite IaC declaring so.
- Verify script discrepancies between declared and actual state.
- For GitHub labs: rate-limit issues, GitHub API behaviors that differ from what the lab assumed.

**What it cannot catch.**

- Issues specific to other accounts (this account's organization SCPs may differ from a target user's).
- Time-bound or rate-limited issues that don't surface in the test run.

**Failure routing.** Real-platform failures are heavyweight signals. They typically indicate the agent missed something the static layers couldn't catch.

- IAM/RBAC permission failures → per-phase or Lab-level Generator (likely IaC issue).
- Attack script runtime errors → per-phase Generator.
- Verify script mismatches → either Lab-level Generator (verify logic wrong) or per-phase Generator (state actually not produced).

**Per-step gating (per `architecture.md §0.7` emergent class principle).** Steps marked `full` get applied; steps marked `partial_simulation` get applied with mocks; steps marked `demonstration_only` get skipped at Layer 4 (they don't execute against real platforms). The Layer 4 verdict becomes "all `full` and `partial_simulation` steps applied successfully; `demonstration_only` steps documented but not executed."

**Per-platform confirmation (friction, not enforcement).** Layer 4 is gated by `--apply`. The user must additionally pass `--i-confirm-this-is-a-lab-environment-for-<platform>` for each platform the lab targets (e.g., `--i-confirm-this-is-a-lab-environment-for-aws`). Optional friction-layer heuristics — non-production-account-pattern checks, expensive-resource scans — may be applied per platform, but the heuristic is friction, not enforcement. The flag is the operator's signed acknowledgment of responsibility. The heuristics catch accidents (running `--apply` against a wrong AWS profile); they do not prevent deliberate misuse.

**V2 additional safety boundaries (rationale for v1 deferral):**

- **Mandatory pre-apply user confirmation,** showing the user exactly what will be created.
- **Mandatory post-apply verification** before any cleanup runs — Layer 4 confirms the resources it expected to create were actually created before destroying them.
- **Refusal to apply when cleanup is structurally incomplete** — Layer 2 verifies cleanup coverage of all `produces_world_state` items; if Layer 2 flagged any uncovered world-state items, Layer 4 refuses to apply.

These boundaries address the v1 deferral concern (orphaned cloud resources from broken cleanup).

**Timing (in v2 when active).** Layer 4 runs **once after the static layers (1, 2, 3, 5) converge** — not on every refinement iteration. If Layer 4 fails, a small bounded number of additional refinement iterations may be triggered (each consuming real cloud spend; the user opted into Layer 4 and accepts the cost).

**Notes.**

- Layer 4 is opt-in via `--apply` (in v2). Default is off. The user makes an explicit, deliberate choice to spend cloud/platform money on validation.
- Eval harness can run Layer 4 in batch mode against dedicated cyberlab-gen-eval accounts per platform; this is part of the eval infrastructure, not user-facing (`eval.md §7.11`).
- The cloud-resource budget (Layer 4-related) is independent of the LLM-token budget. Both have configurable caps and are surfaced separately to the user. In v1, cloud-resource budget doesn't apply since Layer 4 doesn't run.

### 6.8 Layer 5: Safety scans

**What.** Scans generated artifacts for accidental dangerous content. The system shouldn't accidentally include real credentials, malware signatures, or content that would harm a user's host machine.

#### Ground truth: the canonical lab-credentials catalog

Labs need to plant credentials that look real enough to trip detection tools (so detection-engineering labs actually demonstrate detection). But the system must not generate or include real credentials. The architecture resolves this with a **canonical lab-credentials catalog** — a bundled list of test/example patterns per platform (see `schema.md §4.11` for the registry placement). The catalog:

- Passes real-credential pattern/entropy checks, so `trufflehog`, `gitleaks`, and similar tools detect them in labs (and the lab's own detection-engineering scenarios work).
- Is publicly documented as fakes (AWS uses `AKIAIOSFODNN7EXAMPLE` / `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`; GitHub uses `ghp_test_*` prefixes; Azure uses well-known test GUID patterns; npm uses `npm_test_*` prefixes).
- Is deterministic (the Generator always uses the same canonical patterns; the lab's detection steps know what to look for).

**Layer 5's actual job:** run credential scanners against generated lab content; whitelist matches against the canonical catalog; flag everything else.

**Inputs.** All generated files. The canonical lab-credentials catalog. The configurable forbidden-pattern list.

**Output.** Pass/fail with specific findings, each categorized by severity.

**Tools.**

- Credential scanners: trufflehog, gitleaks, or equivalent OSS credential scanners. (The list is OSS-only by default; commercial scanners with restrictive licensing — ggshield, for instance — are not bundled but may be used by maintainers of forks under their own license terms.)
- The canonical lab-credentials catalog (for whitelisting matches that are intentional planted fakes).
- A configurable forbidden-pattern list covering all three clouds + GitHub:
  - Real AWS account IDs not in the lab's own outputs.
  - Real Azure tenant IDs / subscription IDs.
  - Real GCP project IDs / service account email patterns of known production projects.
  - Real-world API key formats for AWS / Azure / GCP / GitHub / npm.
- A check that all "credentials" in seeded files match the canonical lab-credentials catalog patterns.
- A check that no generated code reads from `~/.aws/credentials`, `~/.azure/`, `~/.config/gcloud/`, `~/.ssh/`, or other host paths *outside* of what the lab's own setup explicitly authorized.
- **File-system scope check (pattern-based, not behavioral).** Layer 5 statically inspects shell scripts and Python code for path patterns matching host directories outside the working directory plus `produces_world_state` declared paths. This is pattern-based, not behavioral, and can be evaded by dynamic path construction (`rm -rf "$HOME/$VAR/$RAND"` is hard to analyze statically). Consistent with §6.3's "we catch accidents, not adversaries" posture — Layer 5 surfaces the obvious cases, not all possible exploitations.

**Cost.** Seconds. Cheap.

**When Layer 5 runs against `fix_history.json`.** The fix-pipeline workflow involves user-pasted content that may contain credential fragments. Layer 5 against `fix_history.json` runs in three distinct cases:

- **During generation:** `fix_history.json` doesn't exist yet; Layer 5 doesn't scan it.
- **During fix patch validation:** Layer 5 scans the proposed patch *and* the new `fix_history.json` entry being written (since it'll be persisted).
- **During explicit `cyberlab-gen validate <lab-dir>`:** Layer 5 scans the entire lab including `fix_history.json` if it exists.

**Complementary to the Repair Agent's during-session detector.** The Repair Agent has a heuristic credential-paste detector (`agents.md §5.16`) that warns the user *during* a paste. Layer 5 catches *after* if the warning fired-and-was-ignored or didn't fire. The two mechanisms work together: live warning during paste, post-hoc scan during validation.

**What it catches.**

- A real cloud access key accidentally embedded as a fake credential (i.e., credential-shaped content that doesn't match canonical catalog patterns and *does* match real-credential heuristics).
- A real account/tenant/project ID matching a known organization leaked into the docs.
- Code that reads the user's actual credential files or SSH keys.
- Code that runs `rm -rf` or equivalent destructive commands on host paths.
- Generated payload files that contain known-malicious signatures (Sigma matches, YARA hits).
- Content that pattern-matches to phishing kits, ransomware, or other categories outside cyberlab-gen scope.
- User-pasted credential fragments in `fix_history.json` (from Repair Agent sessions where the user pasted error messages containing partial credentials).

**What it cannot catch.**

- Sufficiently novel malware. (This is a defense-in-depth layer, not a malware scanner.)
- Sophisticated host-system attacks that don't trip pattern matchers.
- The fundamental "is this educational vs. operational tooling" question — that's what scope (`architecture.md §0.2`) and the Critic-as-peer-stage are for. Layer 5 catches *accidents*, not intent.

**Severity routing.**

- **High severity** (credential-shaped content not in the canonical catalog and matching real-credential heuristics; host-system attack pattern): pipeline halts, lab is *not* shipped, report explains. User must investigate.
- **Medium severity** (suspicious pattern; user-paste in `fix_history.json` that looks like a credential fragment from an error message): pipeline ships the lab with the finding flagged and a recommendation to review.
- **Low severity** (false-positive likely): logged, no flag.

**Layer 5 high-severity is the only v1 case where a generated lab does not ship.** All other failures (mechanical-layer failures after exhausted refinement, Critic rejection after exhausted refinement) ship the lab with prominent flags and rely on the user to decide. Layer 5 high-severity is different because the failure indicates a security boundary breach (real credentials in lab output, or host-attack patterns) — the system halts deliberately. See `architecture.md §0.5 criterion 2` for the always-ship-with-flags model and its single exception.

**The refinement loop does not run on Layer 5 high-severity failures; the system halts.** This is a security boundary, not a quality issue. Refinement is for quality; safety is a halt-point.

**Notes.**

- The credential-scanner tool list and severity floors are encoded in the validator implementation; updates are release events.
- The forbidden-pattern list is local to the user's installation but ships with sensible defaults covering all three clouds plus GitHub.
- Layer 5 is the last layer to run because it's checking for *accidents*, not deliberate output. If Layers 1–3 produced a lab that contains real credentials, that's a serious failure and the system should surface it loudly.

### 6.9 Validator report shape

After all layers run, the Validator emits a structured report:

```yaml
report_version: 1
lab_id: <lab-id>
generated_at: <timestamp>
overall_verdict: passed | passed_with_warnings | failed
layers:
  - layer: 1
    name: static_schema_validation
    attempted: true
    verdict: passed
    duration_seconds: 0.4
    findings: []
  - layer: 2
    name: semantic_cross_check
    attempted: true
    verdict: passed_with_warnings
    duration_seconds: 1.2
    findings:
      - severity: warning
        code: declared_world_state_not_in_cleanup
        location: phase_3.produces_world_state[2]
        message: "Phase 3 declares it produces aws_iam_access_key but neither its per-phase cleanup.sh nor the lab-level cleanup.sh has a command for it."
        recommended_action: "Re-run phase 3's per-phase Generator (which owns its per-phase cleanup.sh)."
  - layer: 3
    name: containerized_dry_run
    attempted: true
    verdict: passed
    duration_seconds: 87.4
    findings: []
  - layer: 4
    name: real_platform_apply
    attempted: false
    reason: "Layer 4 (real-platform apply) is deferred to v2; see architecture.md §8.1"
  - layer: 5
    name: safety_scans
    attempted: true
    verdict: passed
    high_severity_findings: 0
critic_summary:                                    # populated separately by Critic stage
  overall_score: 0.84
  dimensions:
    fidelity_to_blog: 0.91
    completeness: 0.78
    implementation_correctness: 0.88               # against attack semantics, not Layer 2 territory
    code_quality: 0.85
    doc_quality: 0.82
    cleanup_quality: 0.80
  per_phase_confidence:
    - phase_id: phase_1_initial_access
      confidence: 0.92
      concerns: []
    - phase_id: phase_3_persistence
      confidence: 0.65
      concerns:
        - "Timing-sensitive setup step may need user adjustment for slower regions"
  flagged_concerns: [...]
  verdict: approve
validated_static_only: true                        # derived field, see notes
iteration_history_ref: ".cyberlab-gen/refinement_history.json"
```

**Field semantics.**

- **`attempted`** is universal across layers — `true` when the layer ran, `false` when it was skipped or deferred. Always present on every layer entry. This makes the report shape stable across v1 (where Layer 4 is always `attempted: false`) and v2 (where Layer 4 may run).
- **`validated_static_only`** is a derived field computed from "Layer 4 was not attempted." Tools consuming the report use this convenience flag rather than re-computing from layer details. Do not author it manually; the Validator computes it.
- **`iteration_history_ref`** points to `.cyberlab-gen/refinement_history.json` which contains the per-iteration causality log from the refinement coordinator (per `pipeline.md §3.2.12`). The validator report does not duplicate iteration data; readers needing iteration context follow this reference.

**Persistence locations.**

- **Machine-readable:** `.cyberlab-gen/validation_report.json` (JSON for tool consumption).
- **Human-readable:** `validation-report.md` at the **lab root**, always generated.

The Critic's per-phase confidence appears in both. The README's "How to use this lab" section is generated from the same per-phase confidence data (per `agents.md §5.13`).

### 6.10 Refinement loop integration

The refinement loop coordinator (`agents.md §5.15`, specified in `pipeline.md §3.2.12`) consumes the Validator's report plus the Critic's verdict. Its routing decisions:

- **Any Layer 1 failure** → re-run upstream agent (Extractor or Planner depending on which schema) via the stage's *retry* mechanism, **not refinement**. Layer 1 failures are structural — the agent emitted malformed output — and retry is the appropriate mechanism (stage-local, default 3 attempts per `architecture.md §1.7`). Refinement is for quality/judgment failures, not structural ones. If the agent can't produce schema-valid output within its retry budget, the pipeline halts with a structured error rather than escalating to refinement.
- **Any Layer 2 failure** → re-run the implementation agent (per-phase Generator, Lab-level Generator, Cleanup Generator, or Docs Generator depending on the mismatch).
- **Layer 3 failure** → re-run the agent that produced the failing file. If multiple files fail, prefer to re-run them in order (per-phase first, then Lab-level, then Cleanup, then Docs).
- **Layer 4 failure** → not applicable in v1 (Layer 4 deferred to v2 per §6.7).
- **Layer 5 high-severity** → **halt. No refinement.** This is a security boundary, not a quality issue.
- **Layer 5 medium-severity** → ship the lab with the finding flagged in the report; no refinement triggered.
- **Critic `refine` verdict** → re-run agents per the Critic's recommendations. Bounded by both the per-agent cap (5 iterations per agent) and the total cap (20 iterations), per `architecture.md §1.7`. The total cap typically binds first; the per-agent cap is a fairness mechanism.
- **Critic `reject` verdict** → treated as `refine` for refinement-loop purposes (try to address the concerns within budget); on budget exhaustion with reject persisting, lab ships with prominent rejection notice in `validation-report.md` (per `agents.md §5.14`). The user decides whether to use the lab (with `fix` mode for runtime issues) or regenerate.

**Same-root-cause finding deduplication.** When Layer 2 and the Critic both flag findings on the same artifact (e.g., Layer 2 flags "cleanup script missing IAM access key handling" mechanically while the Critic flags "cleanup is incomplete for credential-related world state" semantically), the coordinator deduplicates and routes once. Two cases:

- *Same target agent* — both findings would re-run the same agent. Route once with both findings as combined feedback.
- *Different target agents* — Layer 2 targets the per-phase Generator (mechanical mismatch); the Critic targets a different agent (e.g., Cleanup Generator for orchestration concerns). The coordinator routes to the upstream agent first per the cascade-handling principle in `pipeline.md §3.2.12` — fixing root-cause upstream is cheaper than letting downstream agents iterate around an upstream problem.

The deduplication is recorded in the iteration-causality log so the user can see in the run report which findings were merged and why.

The coordinator also tracks oscillation per `pipeline.md §3.2.12` (cycle / phase-level repeat / cascade) using its iteration-causality log.

### 6.11 What the validator does not do

A few things deliberately outside the Validator's scope:

- **It does not assess pedagogical value.** Whether a lab teaches its concepts effectively is the Critic's job, not the Validator's. The Validator checks correctness, not effectiveness.
- **It does not run static analyzers at "best practices" strictness.** The configured strictness is "no obvious bugs," not "passes every rule." Stricter is configurable per project.
- **It does not check the source blog's correctness.** If the blog itself contains technical errors, the lab will faithfully reproduce them. The system aims for fidelity to source, not source-correction.
- **It does not enforce code style preferences.** It enforces that code parses, runs, type-checks, and matches declared interfaces. Beyond that, generated code is what it is.
- **It does not assess whether fallback decisions per `schema.md §4.20` were reasonable.** That's the Critic's job. The Validator confirms that the resulting code is correct against the manifest's declarations, regardless of which fallback was taken.
- **It does not run the lab against real platforms in v1.** Layer 4 (real-platform apply) is v2-deferred per `architecture.md §8.1`. In v1, real-platform validation is the user's responsibility, with `cyberlab-gen fix` mode (`pipeline.md §3.4`) for runtime issues.

### 6.12 Section summary

The Validator runs four mechanical layers in v1 (1, 2, 3, 5); Layer 4 (real-platform apply) is deferred to v2. The Critic runs as a peer stage (not a layer within the Validator) and feeds the refinement coordinator. Cheap layers run first; expensive layers only run when cheap layers pass. Each layer has a defined failure-routing target so the refinement loop knows which agent to re-run.

Layer 1 (static schema) catches most generation errors at near-zero cost and enforces the `spec_kind` discriminator. Layer 2 (semantic cross-check) enforces the manifest as single source of truth and verifies the `references_lab_outputs` contract from the Planner. Layer 3 (containerized dry-run) catches runtime errors without spending platform money, with per-cloud tflint plugins for cross-cloud validator coverage, respecting per-step reproducibility. Layer 4 (real platform apply) is deferred to v2 per §6.7; in v1 real-platform validation is the user's responsibility, with `cyberlab-gen fix` mode for runtime issues. Layer 5 (safety scans) is the security boundary; it scans for credential-shaped content and whitelists matches against the canonical lab-credentials catalog (§6.8), halting the pipeline only on credential patterns that don't match canonical fakes — the one case in v1 where a generated lab does not ship.

The Validator is framework code. Its strictness, rule sets, and forbidden-pattern lists are versioned with the codebase and updated as release events. The Validator's report is auditable and persisted alongside the lab (`.cyberlab-gen/validation_report.json` machine-readable; `validation-report.md` at lab root human-readable; both include per-phase confidence from the Critic).

The layered safety model (per `architecture.md §1.6` honest framing) combines scope, ingestion notices, mechanical scans, and the Critic to provide defense in depth for the well-intentioned user. It does not claim to defend against a determined adversary.

---

*End of validation document. See `agents.md §5.14` for the Critic's contract, `pipeline.md §3.2.12` for the refinement loop, and `eval.md` for how the eval harness measures validator behavior.*
