# Blog walk: `confusedfunction-gcp-cloud-functions-privesc`

> **Ground-truth reference — human-reviewed (2026-06-20).** This walk was
> agent-drafted from the Tenable source blog, then reviewed against the source in
> the human ground-truth pass and corrected; it now stands as **blessed ground
> truth** for Extractor/Planner scoring. Distinct gate: the eval **calibration
> values** (CALIBRATION.md) remain pending the separate paid `--stage plan` run —
> the human-pass gate is cleared, the calibration gate is not (ADR 0104).

A manual ground-truth reading of a curated blog. The walker reads the blog
end-to-end and maps its narrative onto the AttackSpec structure
(`docs/schema.md §4.8`). This entry adds **`cloud:gcp` coverage** to a curated
set that was otherwise all-AWS.

---

## 1. Header

- **id:** `confusedfunction-gcp-cloud-functions-privesc` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `vulnerability_disclosure` (resolved per ADR 0103 — shape is a
  descriptive open-set label, not a closed enum). This is a 100% GCP
  confused-deputy privilege-escalation disclosure; the honest descriptive value
  is used directly rather than forcing the AWS-flavored trio token. Matches
  `eval/blog-sets/manifest.yaml`. See §15.
- **URL:** https://www.tenable.com/blog/confusedfunction-a-privilege-escalation-vulnerability-impacting-gcp-cloud-functions
- **Canonical URL:** https://www.tenable.com/blog/confusedfunction-a-privilege-escalation-vulnerability-impacting-gcp-cloud-functions (same)
- **Title:** "ConfusedFunction: A Privilege Escalation Vulnerability Impacting GCP Cloud Functions"
- **Publisher:** Tenable Research
- **Publisher kind:** `vendor_lab`
- **Authors:** Liv Matan
- **Publication date:** 2024-07-24
- **Accessed date:** 2026-06-19
- **Content hash:** TBD (the Extractor produces this mechanically)

## 2. One-paragraph summary

Tenable Research disclosed "ConfusedFunction," a privilege-escalation flaw in
Google Cloud Platform Cloud Functions. When a user creates or updates a Cloud
Function, GCP transparently spins up a Cloud Build instance to build the
function into a container image and push it to Container/Artifact Registry, and
automatically attaches a high-privilege default Cloud Build service account to
that build — without the user's prior visibility or control. An attacker who
holds only Cloud Function create/update permission can plant a malicious npm
dependency (or a malicious `gcp-build` script) in the function package; during
deployment Cloud Build runs `npm install`, which executes the attacker's
`preinstall` script inside the build container, reads the default Cloud Build
service account token from the compute metadata endpoint, and lets the attacker
impersonate that far-more-privileged service account to reach Cloud Storage,
Artifact Registry, and Container Registry. Google partially remediated the issue
in mid-June 2024 by letting deployments use a custom (and, for new projects, the
Compute Engine default) service account, but pre-existing "legacy" Cloud Build
service accounts remain affected.

## 3. Thesis

- **Types:** `privilege_escalation`, `vulnerability_chain`,
  `cloud_provider_flaw`, `misconfiguration`, `ttp_chain`. (The primary reading is
  `privilege_escalation` + `vulnerability_chain`; `cloud_provider_flaw` captures
  that the root cause is the platform's own deployment design.)
- **Summary:** A confused-deputy-style design flaw in GCP Cloud Functions
  deployment: the platform silently attaches a high-privilege default Cloud Build
  service account to a build the customer cannot inspect beforehand, while the
  function source (and its dependency-install and build scripts) is fully
  attacker-controllable. A principal holding only
  `cloudfunctions.functions.create`/`update` can therefore execute code inside
  the privileged Cloud Build container, steal that service account's token from
  the metadata service, and escalate to the Cloud Build SA's much broader
  permissions across Cloud Storage, Artifact Registry, and Container Registry.
- **Attacker objective:** Escalate from a low-privilege identity that can only
  create/update a Cloud Function to the permissions of the high-privilege default
  Cloud Build service account, gaining access to other GCP services (Cloud
  Storage, Artifact Registry, Container Registry) created during deployment.
- **Vulnerability story:** Substantive. This is a genuine
  vulnerability-disclosure, not a pure TTP chain. **Root cause:** in the Cloud
  Functions deployment flow GCP's service agents automatically attach a default
  Cloud Build service account with "excessive permissions" to a Cloud Build
  instance whose inputs (`package.json` dependencies, `preinstall`/`gcp-build`
  scripts) are entirely controlled by the function's source code. Before the fix
  the customer had no visibility or control over which service account was
  attached. Because the attacker controls what runs during the build and the
  build runs as a privileged identity, the gap between the caller's privilege
  (function create/update) and the build's privilege (Cloud Build SA) is the bug.
  The blog never enumerates the exact roles/permissions of the default Cloud
  Build SA (it only says "excessive permissions"), and assigns **no CVE**.
  Google's mid-June-2024 mitigation lets deployments specify a custom service
  account (new projects default to the Compute Engine SA for directly-submitted
  builds), but it "did not address existing Cloud Build instances" — those legacy
  SAs remain affected, so the fix reduced but did not eliminate severity. The
  vulnerability affects both first- and second-generation Cloud Functions; the
  blog walks gen1 because gen2 involves additional services.

## 4. Chain steps

Six ordered steps. The chain is canonical and linear: a low-privilege identity
triggers a privileged build it controls, executes code inside it, steals the
build SA's token, and impersonates it.

### Chain step 1: Obtain a low-privilege identity with Cloud Function create/update permission

- **Blog excerpt:**
  > An attacker who gains access to create or update a Cloud Function can take advantage of the function's deployment process to escalate privileges
- **Description:** The attacker starts from a principal (user or service
  account) holding only ordinary Cloud Functions create/update permission
  (`cloudfunctions.functions.create` or `cloudfunctions.functions.update`) in the
  target project. Critically, this principal does **not** need Cloud Build, Cloud
  Storage, or Artifact/Container Registry permissions of its own. This is the
  assumed starting position, not an action the blog teaches how to achieve.
- **MITRE techniques:** [`T1078.004`] *(walker-inferred — valid accounts / cloud; not stated in blog)*
- **Preconditions:** Attacker controls a principal bound to a role granting only
  Cloud Function create/update in the target GCP project.
- **Postconditions:** Attacker can submit a function create/update request.
- **Outputs (value-type names):** [`gcp_credentials`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** A lab can provision a GCP service
  account / user bound to a custom role granting only
  `cloudfunctions.functions.create`/`update` and use it as the attacker identity.
  This is a vanilla IAM binding, fully scriptable with no provider-internal
  dependency.

### Chain step 2: Deploy/update a Cloud Function, transparently triggering a privileged Cloud Build with a default service account attached

- **Blog excerpt:**
  > a Cloud Build instance orchestrates the function's deployment. It does so by building the Cloud Function code into a container image and pushing the image to a Container Registry or an Artifact Registry.
- **Description:** The attacker creates or updates a Cloud Function. GCP's service
  agents transparently spin up a Cloud Build instance to build the function
  source into a container image and push it to Container Registry or Artifact
  Registry, and automatically attach the high-privilege default Cloud Build
  service account to that build. The blog stresses the customer had no prior
  visibility or control over this attachment. This is the confused-deputy
  precondition the attacker relies on.
- **MITRE techniques:** [`T1648`, `T1525`] *(walker-inferred — serverless execution; implant internal image)*
- **Preconditions:** Step 1 (can create/update a function); the project uses the
  privileged default Cloud Build SA (the legacy / pre-fix default).
- **Postconditions:** A Cloud Build instance exists, running attacker-supplied
  source, with the default Cloud Build SA attached.
- **Outputs (value-type names):** [`gcp_cloud_build_instance_reference`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Deploying a real Cloud Function and
  observing the auto-created Cloud Build with a default Cloud Build SA attached is
  fully reproducible in a lab GCP project (the legacy default behavior persists
  for existing projects per the blog; a lab can recreate the pre-fix default-SA
  condition). No victim-specific or destructive element.

### Chain step 3: Inject a malicious npm dependency with a preinstall script into the function package

- **Blog excerpt:**
  > We can do that using a preinstall script in our malicious dependency that will force the Cloud Build instance to run our code.
- **Description:** Because the function source is fully attacker-controlled, the
  attacker adds an attacker-controlled npm package (e.g.
  `mypocmaliciouspackage`) to the function's `package.json` dependencies. That
  package carries a `preinstall` lifecycle script that executes automatically when
  Cloud Build runs `npm install` during deployment. *(The blog documents an
  equivalent variant using the `gcp-build` script in `package.json` — see §5.)*
  This weaponizes the build inputs the attacker controls.
- **MITRE techniques:** [`T1195.001`, `T1059.004`] *(walker-inferred — supply-chain compromise of dev tools; unix shell)*
- **Preconditions:** Step 1 (control of the function source package).
- **Postconditions:** The function package contains an attacker dependency whose
  `preinstall` script will run inside the build.
- **Outputs (value-type names):** [`malicious_npm_package`, `gcp_cloud_function_source_package`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Authoring a malicious npm package with a
  `preinstall` script and referencing it from a function's `package.json` is
  entirely attacker/lab-side and fully scriptable; no GCP-internal behavior is
  needed to stage this artifact.

### Chain step 4: Cloud Build runs `npm install` during deployment, executing the attacker's preinstall script as the Cloud Build SA

- **Blog excerpt:**
  > The deployment process will start, and the Cloud Build instance correlated to our function deployment will run its build commands, including the 'npm install' command
- **Description:** On deployment, the Cloud Build instance runs its build commands
  including `npm install`, which pulls the attacker's dependency and runs its
  `preinstall` script. The attacker's code now executes inside the Cloud Build
  container with the identity/privileges of the attached default Cloud Build
  service account.
- **MITRE techniques:** [`T1059.004`, `T1610`] *(walker-inferred — unix shell; deploy container)*
- **Preconditions:** Steps 2 and 3 (privileged build triggered; malicious
  dependency staged in the source).
- **Postconditions:** Arbitrary attacker code is executing inside the Cloud Build
  container under the Cloud Build SA's context.
- **Outputs (value-type names):** [`gcp_cloud_build_code_execution`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** A lab deployment of the malicious function
  reproduces `npm install` executing the `preinstall` script inside the build
  container with the Cloud Build SA's context. This is the legacy default behavior
  and is reproducible against a lab project configured to use the (legacy) default
  Cloud Build SA.

### Chain step 5: Read the default Cloud Build service account token from the compute metadata endpoint

- **Blog excerpt:**
  > This code will then extract the default Cloud Build service account token, which GCP automatically attached, from the metadata of the Cloud Build instance.
- **Description:** The `preinstall` script queries the GCP compute metadata
  service (`http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`
  with the `Metadata-Flavor: Google` header) to retrieve the OAuth access token of
  the default Cloud Build service account attached to the build instance, then
  exfiltrates it to attacker control.
- **MITRE techniques:** [`T1552.005`, `T1528`] *(walker-inferred — cloud instance metadata API; steal application access token)*
- **Preconditions:** Step 4 (code execution inside the build container).
- **Postconditions:** Attacker holds the default Cloud Build SA's OAuth access
  token.
- **Outputs (value-type names):** [`gcp_service_account_token`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Querying the metadata endpoint for the
  attached SA token from inside the build container is a standard, fully
  reproducible technique against a real lab Cloud Build instance; the token
  retrieved is the lab's own SA token, not a victim secret.

### Chain step 6: Impersonate the Cloud Build service account and escalate privileges to reach Cloud Storage / Artifact Registry / Container Registry

- **Blog excerpt:**
  > Finally, we can use this token to impersonate the identity of the default Cloud Build service account and escalate our privileges from Cloud Function to the permissions of the service account.
- **Description:** The attacker uses the stolen access token to act as the
  high-privilege default Cloud Build service account, gaining its "excessive
  permissions." The blog states the attacker could leverage these privileges in
  other GCP services created when a Cloud Function is created/updated, "including
  Cloud Storage, and Artifact Registry or Container Registry." This is the
  realized privilege escalation: from function-create/update only, up to the
  Cloud Build SA's broad cross-service access. The blog does **not** enumerate
  the exact roles/permissions of that SA (only "excessive permissions").
- **MITRE techniques:** [`T1078.004`, `T1098.003`] *(walker-inferred — valid accounts / cloud; additional cloud roles)*
- **Preconditions:** Step 5 (holds the SA token).
- **Postconditions:** Attacker operates with the Cloud Build SA's permissions
  across Cloud Storage, Artifact Registry, and Container Registry.
- **Outputs (value-type names):** [`gcp_service_account_token`, `gcp_storage_access`, `gcp_artifact_registry_access`]
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Using the captured token to call Cloud
  Storage / Artifact / Container Registry APIs as the Cloud Build SA is fully
  reproducible in a lab whose Cloud Build SA holds the (legacy) broad permissions.
  The exact production blast radius depends on the real SA's roles (unspecified by
  the blog), but the escalation mechanic itself is scriptable end-to-end against
  lab resources.

## 5. Alternative paths (optional)

The blog documents **two interchangeable code-execution primitives** that reach
an identical outcome and share every other step in the chain:

- **(a) Canonical (steps 3–4 above):** a malicious npm dependency carrying a
  `preinstall` lifecycle script.
- **(b) Alternative — `gcp-build` script:** a malicious `gcp-build` entry in the
  `package.json` `scripts` block (e.g. a `gcp-build` command that fetches and
  runs an attacker shell script). This is recorded as an **alternative injection
  mechanic within step 3**, not as a separate chain or a separate step: it
  differs from (a) only in *how* attacker code first runs inside the build; steps
  1, 2, 5, and 6 are identical. A naive reading might double-count these as two
  chains or two steps — they are one logical injection step with two
  implementations.

## 6. Facets

### 6.1 `target:*` (Extractor-derived)

`target:gcp`, `target:gcp_cloud_functions`, `target:gcp_cloud_build`.

The attacked surface is GCP only. **No `target:aws`** despite the `aws_ttp`
shape token (see §15).

### 6.2 `runtime:*` (Planner-derived; walker's guess)

`runtime:gcp` (first-class). A single GCP project hosts the entire chain — this
is **not** multi-platform. `gcp` is in the first-class runtime set
(`{aws, azure, gcp, github, local}`), so **no `non_first_class_runtime`** tag
applies.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

`lab_class_signal:vulnerability_chain`,
`lab_class_signal:privilege_escalation`,
`lab_class_signal:requires_infra` (needs a real GCP project with a Cloud
Function + the legacy default Cloud Build SA),
`lab_class_signal:cloud_provider_flaw` (the bug is the provider's deployment
design; customers cannot fully remediate legacy SAs).

## 7. Value types referenced

All are **GCP-namespaced and flagged `(proposed)`** — no existing GCP value-type
registry was assumed; these parallel how the AWS walks proposed `aws_`/`github_`
types.

- `gcp_credentials` `(proposed)` — the attacker's starting low-privilege identity.
- `gcp_service_account_token` `(proposed)` — the pivotal stolen secret (analogous
  to `github_pat` / `aws_credentials` in the AWS walks).
- `malicious_npm_package` `(proposed)` — the attacker-authored dependency.
- `gcp_cloud_function_source_package` `(proposed)` — the attacker-controlled
  function source bundle.
- `gcp_cloud_build_instance_reference` `(proposed)` — handle to the auto-created
  build.
- `gcp_cloud_build_code_execution` `(proposed)` — the code-execution foothold
  inside the build container.
- `gcp_storage_access` `(proposed)` — escalated access to Cloud Storage.
- `gcp_artifact_registry_access` `(proposed)` — escalated access to Artifact /
  Container Registry.

**No `aws_credentials` or any `aws_`-prefixed value type** appears anywhere — a
cross-cloud value-type leak would be a mis-attribution (see §15).

## 8. Defender techniques

Not applicable for this blog — it is a vendor vulnerability-disclosure from the
attacker's perspective with no defender-investigation narrative (no
`incident_analysis` content).

## 9. Defenses

- **Description:** Specify a custom, least-privilege service account for the Cloud
  Build instance used in function deployment (Google's mid-June-2024 option)
  instead of relying on the default Cloud Build SA.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** 2, 4, 6.
- **Description:** For new projects, GCP now defaults directly-submitted builds to
  the Compute Engine service account and requires new triggers to specify a
  service account explicitly.
  - **Applicability:** `vendor_only` (a platform default change shipped by Google;
    customers do not toggle it).
  - **Addresses chain steps:** 2.
- **Description:** Use organization policies (added May–June 2024) to
  control/constrain the default Cloud Build service account and enforce
  least-privilege SA usage.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** 2, 6.
- **Description:** For every Cloud Function still using the legacy Cloud Build
  service account, replace it with a least-privilege service account (the fix did
  not remediate existing/legacy SAs).
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** 2, 6.
- **Description:** Treat background-created resources (Cloud Build, Cloud Storage,
  registries) as in-scope customer-responsibility assets and audit the IAM granted
  to platform-attached service accounts.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** 2, 5, 6.
- **Detection path / format:** Not specified in the blog (no detection signatures
  or log-query formats are given).

## 10. External references

- **CVEs cited:** **None.** ConfusedFunction is a named-but-uncatalogued
  cloud-provider flaw; the blog assigns and references **no CVE** (see the CVE
  trap in §15).
- **Related blogs:**
  - Tenable, "ImageRunner: A Privilege Escalation Vulnerability Impacting GCP
    Cloud Run" — https://www.tenable.com/blog/imagerunner-a-privilege-escalation-vulnerability-impacting-gcp-cloud-run
  - Orca Security, "Bad.Build: Google Cloud Build potential supply chain attack" —
    https://orca.security/resources/blog/bad-build-google-cloud-build-potential-supply-chain-attack-vulnerability/
- **MITRE ATT&CK techniques referenced:** None. The blog cites no MITRE
  techniques; every technique ID in §4 is walker-inferred, not blog-asserted.

## 11. Real-world incidents

- **Status:** `none_observed`
- **Evidence source:** The blog is a proactive vulnerability disclosure by
  Tenable Research; it describes no observed in-the-wild exploitation. No incident
  is claimed or attributed.
- **Incidents:** None.

## 12. Expected lab class

- **Lab kind:** "GCP serverless privilege-escalation / cloud-provider-flaw
  vulnerability-chain lab" — a Cloud Functions → Cloud Build confused-deputy
  escalation provisioned entirely on a single GCP project.
- **Why this class:** The `target:gcp` + `target:gcp_cloud_functions` +
  `target:gcp_cloud_build` facets, combined with
  `lab_class_signal:vulnerability_chain`, `:privilege_escalation`,
  `:requires_infra`, and `:cloud_provider_flaw`, point at a single-cloud
  (`runtime:gcp`, first-class) GCP lab. Every step is a vanilla, scriptable
  GCP/npm operation against a lab project that recreates the legacy default Cloud
  Build SA condition; full reproducibility; medium complexity (6 steps). It is
  not multi-platform and not an incident-analysis lab.

## 13. Reproducibility (lab-level)

- **Classification (lab-level):** `full`
- **Caveats:** All six required steps are `full`: the entire chain is reproducible
  against a lab GCP project provisioned to use the **legacy default Cloud Build
  service account** (the pre-fix default behavior, which the blog confirms still
  applies to existing projects). The one disclosed caveat is **fidelity of
  consequence, not reproducibility of mechanic**: the blog never enumerates the
  default Cloud Build SA's exact roles/permissions (only "excessive permissions"),
  so a lab must **choose** what broad role to grant the SA to mirror the real
  blast radius — that choice is a **lab design decision**, not a blog-stated fact.
  The escalation technique itself reproduces exactly. A lab that only provisions a
  *post-fix* new project (custom / Compute Engine SA) would **not** reproduce the
  privileged-default-SA precondition — the lab must deliberately recreate the
  legacy default-SA condition for step 2 to hold.
- **Derivation trace:** Per the any-heterogeneity rule (`schema.md §4.8` line
  438), all required steps must share a tier to inherit it: step 1 `full`, step 2
  `full`, step 3 `full`, step 4 `full`, step 5 `full`, step 6 `full` → all six
  required steps share `full` (no `demonstration_only` or `not_reproducible`
  steps to exclude, no spread across ≥2 tiers) → lab-level classification
  `full`.
- **Overall assessment:** The full ConfusedFunction chain (low-priv
  function-create identity → trigger privileged Cloud Build → malicious
  dependency/preinstall script → `npm install` code execution as the Cloud Build
  SA → metadata token theft → SA impersonation across Cloud
  Storage/Artifact/Container Registry) is faithfully reproducible in a single GCP
  project lab, provided the lab recreates the legacy default-Cloud-Build-SA
  condition the fix left in place for existing projects. It is non-destructive and
  victim-agnostic; the only abstraction is choosing how broad to make the lab's
  Cloud Build SA, since the blog leaves the production SA's role set unspecified.

## 14. Coverage tags

`cloud:gcp`, `complexity:medium` (6 chain steps, in the 4–8 band),
`thesis:privilege_escalation`, `thesis:vulnerability_chain`,
`thesis:cloud_provider_flaw`, `vulnerability_disclosure`, `serverless`,
`confused_deputy`, `supply_chain_adjacent`.

This walk's headline coverage role is **`cloud:gcp`** — the curated set was
all-AWS before it, so this is the first non-AWS cloud surface. `serverless` and
`confused_deputy` are also new dimensions. No `multi_platform` and no
`non_first_class_runtime` tag (single first-class GCP runtime). No
`incident_analysis` tag (no defender techniques).

## 15. Manual ground-truth notes

**The highest-value section.** What a human reviewer noticed that an LLM Extractor
on the same blog might miss.

### Shape resolved to `vulnerability_disclosure` (ADR 0103)

The documented `shape` trio (`aws_ttp` / `supply_chain` / `incident_analysis`)
has no slot for a GCP confused-deputy vuln disclosure. ADR 0103 found `shape` is
purely descriptive and open-set (nothing in the pipeline or the eval harness
branches on its value), so this walk and the manifest now both use the honest
`vulnerability_disclosure` value directly — no trio member is forced. The earlier
`aws_ttp`-under-protest token is gone, which also removes the trap of an Extractor
reading "aws_ttp" as "this is about AWS": the correct cloud facet is `cloud:gcp` /
`target:gcp` ONLY (no `aws` content is present).

### CVE trap (highest-value warning)

The blog assigns **no CVE** and references none. ConfusedFunction is a
named-but-uncatalogued cloud-provider flaw (cloud bugs frequently get vendor
names, not CVEs, because the vendor fixes them server-side). An LLM Extractor is
at **high risk of hallucinating a CVE** from training-data analogies to other
GCP / Cloud Build issues. Ground truth: `external_references.cves = []`. Treat
any emitted CVE as a hard rejection.

### Default Cloud Build SA role — deliberate underspecification

The blog only ever says the default Cloud Build SA has "excessive permissions"
and "higher privileges than the function creator." It does **not** name the
Editor role, `cloudbuild.builds.builder`, the
`[PROJECT_NUMBER]@cloudbuild.gserviceaccount.com` email format, or any specific
scopes. The real-world default Cloud Build SA historically held the broad Editor
role — but the **blog** does not say so, so it is **not** put in any step or
value type. An Extractor that "fills in" Editor / `cloudbuild.builds.builder` is
importing outside knowledge the blog does not license. This was recorded as a
**fidelity caveat on lab reproducibility** (the lab must *choose* how broad to
make its SA), not as a blog-stated fact — see §13.

### Two attack variants — canonical vs. alternative

The blog documents two interchangeable code-execution primitives reaching the
identical outcome: (a) a malicious npm dependency carrying a `preinstall`
lifecycle script, and (b) a malicious `gcp-build` script in `package.json`'s
`scripts` block. Variant (a) is the canonical chain (steps 3–4); (b) is folded in
as an alternative injection mechanic in §5, rather than a separate
`alternative_paths` branch — they share every other step and differ only in the
one injection mechanic. A naive Extractor might double-count these as two
separate chains or two separate steps; they are one logical step with two
implementations.

### Gen1 vs. gen2

The vulnerability affects **both** first- and second-generation Cloud Functions;
the blog explicitly walks gen1 "as it is less complicated than that of the second
gen, which includes additional GCP services." The chain here is scoped to the
gen1 narrative the blog actually walks. An Extractor should not assume gen2-only
or gen1-only; the flaw spans both, but the step-by-step detail is gen1.

### Partial fix / legacy SA — the reproducibility hinge

Google's mid-June-2024 fix is **partial**. It added a custom-SA option and changed
defaults for **new** projects/triggers (Compute Engine SA), but "did not address
existing Cloud Build instances" — pre-existing "legacy Cloud Build service
accounts" remain affected. This is why the lab is rated `full` rather than
downgraded: the privileged-default-SA precondition is still reproducible by
recreating the legacy condition (or using an unfixed existing project). If a lab
only provisions a post-fix *new* project, step 2's privileged-default-SA
attachment would not occur and the chain breaks — called out explicitly in §13
caveats. An Extractor could wrongly read "fixed in mid-June 2024" as "not
reproducible"; the legacy-SA persistence is the load-bearing nuance.

### Reproducibility tiers — judgment calls

Every step is `full`. Unlike the AWS CodeBuild walk (which had a
`demonstration_only` racing mechanic against real GitHub), nothing here depends on
a non-deterministic external condition, a victim-specific resource, or a
now-closed provider surface a lab cannot recreate. Metadata token theft, npm
`preinstall` execution, and SA impersonation are all standard scriptable
operations against lab-owned GCP resources. The only judgment was whether step 6
should be `partial_simulation` given the unspecified SA roles — kept at `full`
because the escalation **mechanic** reproduces exactly; only the blast-radius
**fidelity** is a lab design choice, which is a caveat, not a tier downgrade.

### Value types — all proposed, GCP-namespaced

All value types are proposed/new (no existing GCP value-type registry assumed),
namespaced `gcp_*` parallel to the AWS walks' `aws_`/`github_` proposals.
`gcp_service_account_token` is the pivotal stolen secret (analogous to
`github_pat`). `aws_credentials` was deliberately **not** emitted anywhere — this
is a pure GCP chain; a cross-cloud value-type leak would be a mis-attribution.

### MITRE — all inferred, none stated

The blog states no MITRE techniques. Every technique ID in §4 is a walker-inferred
mapping. An Extractor should mark MITRE as inferred / not-stated-in-blog, not as
blog-asserted.

### Confused-deputy naming

The blog does not explicitly spell out the "confused" rationale, but the pattern
is textbook confused-deputy: a low-priv principal (function creator) tricks a
higher-priv deputy (Cloud Build SA) into acting on its behalf. This is captured in
`thesis.summary` and the `confused_deputy` coverage tag, but the walk does **not**
assert the blog "explains" the name (it does not).

### Phase 1 prompt-engineering hints

- The Extractor prompt should explicitly forbid synthesizing a CVE when the blog
  assigns none — vendor-named cloud flaws are a recurring CVE-hallucination trap.
- The prompt should not let the model "fill in" the default Cloud Build SA's
  concrete roles from training data when the blog says only "excessive
  permissions."
- The prompt should treat the shape token as a coarse hint, not a cloud-vendor
  signal: an `aws_*` shape on a GCP blog must still produce `target:gcp` /
  `cloud:gcp`, never `aws`.
