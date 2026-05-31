# Blog walk: `aws-codebuild-actor-id-regex-bypass`

A manual ground-truth reading of Wiz Research's "CodeBreach" disclosure,
mapping the blog's narrative onto the AttackSpec structure
(`docs/schema.md §4.8`).

---

## 1. Header

- **id:** `aws-codebuild-actor-id-regex-bypass` (matches
  `eval/blog-sets/manifest.yaml`)
- **shape:** `supply_chain` (see §15 for the shape-classification
  judgment call — none of the Phase-0 shapes fits cleanly)
- **URL:** https://www.wiz.io/blog/wiz-research-codebreach-vulnerability-aws-codebuild
- **Canonical URL:** same as above (no observed redirect)
- **Title:** "CodeBreach: Infiltrating the AWS Console Supply Chain
  and Hijacking AWS GitHub Repositories via CodeBuild"
- **Publisher:** Wiz (Wiz Research blog)
- **Publisher kind:** `vendor_lab`
- **Authors:** Yuval Avrahami, Nir Ohfeld
- **Publication date:** 2026-01-15
- **Accessed date:** 2026-05-20
- **Content hash:** TBD (Extractor produces mechanically; not computed
  for this walk)

## 2. One-paragraph summary

Wiz Research found seven AWS-owned GitHub repositories with public AWS
CodeBuild project pages, four of which ran builds on pull requests
behind an `ACTOR_ID` regex filter that locked builds to a list of
approved maintainers. Because the filter patterns were not anchored with
`^…$`, *any* GitHub user whose numeric ID merely *contained* a trusted
maintainer's ID matched the filter — and GitHub allocates numeric IDs
sequentially, so a longer ID containing a given 6-digit maintainer ID
becomes available for registration approximately every five days (Wiz
coined this recurring window the "eclipse"). Wiz won the race for one
such ID by automating GitHub Apps manifest-flow registrations
(which generate bot users atomically at confirm-URL visit) and
flooding ~200 confirmation URLs at the right moment to capture target
ID 226755743; they then submitted a PR to `aws/aws-sdk-js-v3` from the
bot user, triggered a CodeBuild build whose payload dumped the build
container's process memory, exfiltrated the `aws-sdk-js-automation`
GitHub Classic PAT (`repo` + `admin:repo_hook` scopes) with full repo
admin, and proved they could invite a fresh attacker account as
collaborator. Wiz halted before publishing a malicious npm release,
reported on 2025-08-25, and AWS anchored the filter, revoked the PAT
on 2025-08-27, and added memory-dump hardening in September; public
disclosure was 2026-01-15.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types:** `vulnerability_chain`, `supply_chain_compromise`,
  `cloud_provider_flaw`, `ttp_chain`, `privilege_escalation`.
- **Summary:** A subtle webhook-filter regex flaw in AWS-managed
  CodeBuild projects (missing start/end anchors), combined with
  GitHub's sequential numeric ID allocation and the atomic
  bot-user-creation primitive in the GitHub Apps manifest flow,
  permitted an unauthenticated attacker to (a) register a GitHub bot
  user whose ID satisfied the filter, (b) trigger a privileged
  CodeBuild build via PR, (c) exfiltrate a long-lived GitHub PAT
  belonging to `aws-sdk-js-automation` from the build container's
  process memory, and (d) gain full repository-admin control over
  `aws/aws-sdk-js-v3` — a precondition for publishing a malicious
  npm package to the wide population of cloud users the blog
  frames as 66% of cloud environments.
- **Attacker objective:** Obtain write/admin control over an
  AWS-owned, npm-published OSS repository in order to position for a
  downstream supply-chain compromise; Wiz's blog stops at
  position-for-compromise rather than executing it. The blog gives
  one quantitative blast-radius framing for the canonical target
  (`aws-sdk-js-v3`): "66% of cloud environments include the
  JavaScript SDK."
- **Vulnerability story:** Two compounding issues. (1) AWS-managed
  CodeBuild webhook `FILE_PATH` / `ACTOR_ID` filters were authored
  without `^` and `$` anchors, so regex-engine `match` semantics
  matched on *substring containment* rather than exact equality —
  meaning the target ID `226755743` matched a filter intended to
  admit only an enumerated set of 6-digit maintainer IDs because
  one of those maintainer IDs appeared as a substring within
  `226755743`. (The blog discloses the target ID but does not
  disclose the specific maintainer-ID substring.) (2) GitHub
  allocates sequential numeric user IDs ("Every user is given a
  unique and sequential numeric ID"), and the blog further confirms
  that org IDs share the user pool; App-bot IDs created via the
  GitHub Apps manifest flow appear to allocate from the same
  sequence — this is operationally required for the race mechanic
  to be feasible, but the bot-pool/user-pool unification is not
  explicitly stated by the blog and is walker inference from the
  race's feasibility. The GitHub Apps manifest flow allows *atomic*
  bot-user creation at a final confirmation URL, making ID racing
  operationally feasible if an attacker pre-stages many manifest
  flows and visits them in a burst at the right moment in the
  sequence. The blog dubs the ~5-day recurring window in which a
  containing ID becomes registerable per 6-digit target ID an
  "eclipse." AWS owns mitigation (1); only GitHub could change (2),
  though customer-side practices (unique per-project fine-grained
  PATs, PR comment-approval gates) reduce blast radius.

## 4. Chain steps

Wiz's narrative resolves into eleven discrete attacker actions. Steps
are ordered as they appear in the post.

### Chain step 1: Discover public CodeBuild project pages on AWS-owned GitHub repos

- **Blog excerpt:**
  > "We quickly found seven AWS-owned repositories with public
  > CodeBuild pages. Of those, four were active and configured to
  > run builds on pull requests."
- **Description:** The attacker enumerates AWS-owned GitHub
  organizations (`aws`, `awslabs`, `corretto`, and others) for
  CodeBuild project pages exposed to the public internet — a
  CodeBuild deployment pattern that exposes build configuration,
  triggers, and historical build metadata without authentication.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Public internet access; awareness that AWS-owned
  open-source repositories exist and may host CodeBuild pipelines.
- **Postconditions:** Knowledge of four PR-triggered CodeBuild
  projects on AWS-owned repos (`aws/aws-sdk-js-v3`, `aws/aws-lc`,
  `corretto/amazon-corretto-crypto-provider`,
  `awslabs/open-data-registry`) and their visible filter
  configurations.
- **Outputs (value-type names from `schema.md §4.12`):**
  `[PROPOSED: aws_codebuild_project_reference]`,
  `[PROPOSED: github_repo_reference]`.
- **Reproducibility:** `partial_simulation`.
- **Why this tier (not a higher one):** A lab can stand up a
  CodeBuild project with a public page and demonstrate the
  enumeration technique, but cannot reproduce discovery against the
  *actual* AWS-owned projects (now patched and partially
  un-publicised).

### Chain step 2: Read the `ACTOR_ID` webhook filter on each project

- **Blog excerpt:**
  > "All four projects implemented an `ACTOR_ID` filter, locking
  > down builds to a list of approved maintainers."
- **Description:** The attacker inspects each project's PR-trigger
  configuration and observes that builds are gated by an
  `ACTOR_ID` filter that enumerates a list of trusted maintainer
  IDs.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 1.
- **Postconditions:** The attacker knows each project's list of
  trusted maintainer IDs (six digits each).
- **Outputs:** `[PROPOSED: github_webhook_filter]`,
  `[PROPOSED: github_user_id]` (the maintainer IDs).
- **Reproducibility:** `full` against a lab CodeBuild project
  configured identically.
- **Why this tier (not a higher one):** The reading is mechanical;
  no AWS-side state is required beyond a lab project.

### Chain step 3: Identify the unanchored-regex flaw

- **Blog excerpt:**
  > "The regex patterns weren't anchored. Without the start `^` and
  > end `$` anchors to require an exact match, a regex engine
  > doesn't look for a string that perfectly matches the pattern,
  > but one that merely _contains_ it."
- **Description:** The attacker recognizes that the filter pattern
  permits any GitHub actor ID that *contains* (rather than
  *exactly equals*) a trusted maintainer ID — and that GitHub IDs
  are integers with no upper bound, so longer containing IDs
  exist.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 2; basic regex knowledge.
- **Postconditions:** A working theory: register a GitHub user (or
  bot) with an ID containing a trusted maintainer ID, and the
  filter will pass that user's PRs.
- **Outputs:** None typed; the output is conceptual.
- **Reproducibility:** `full` (purely analytical against the lab
  filter configuration).
- **Why this tier (not a higher one):** No reason to downgrade — a
  lab can show the bug in identical form.

### Chain step 4: Establish the "eclipse" window for ID availability

- **Blog excerpt:**
  > "At that rate, for any given 6-digit maintainer ID, a new,
  > longer ID containing it would become available for registration
  > approximately every five days. We dubbed this recurring window
  > of opportunity an 'eclipse.'"
- **Description:** The attacker measures GitHub's sequential ID
  allocation rate and computes how often a fresh allocatable ID
  containing a given 6-digit target ID becomes registerable. Wiz
  coins the term "eclipse" for this recurring window.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 3; ability to observe GitHub's
  current ID allocation pointer over time.
- **Postconditions:** A schedule of upcoming eclipses per target
  maintainer ID; the attacker can plan when to race.
- **Outputs:** None typed; the output is a temporal schedule.
- **Reproducibility:** `demonstration_only`.
- **Why this tier (not a higher one):** Real GitHub-ID racing
  depends on real GitHub's real allocation rate; a lab cannot
  reproduce the temporal mechanics. The math and the observation
  technique (polling for the current high-water-mark ID) can be
  illustrated meaningfully per `schema.md §4.20`'s
  "demonstration-must-be-meaningful" floor.

### Chain step 5: GitHub Enterprise org-creation API — rejected as bot primitive, repurposed as ID-sampling probe

- **Blog excerpt:**
  > "Our first thought was to use the GitHub Enterprise API to
  > create organizations, which share the same ID pool as users.
  > While this could allow us to claim the target ID, GH
  > organization accounts can't open pull requests, making them
  > useless for the final exploit. It wasn't a total dead end
  > though. We repurposed this API into an **ID sampling tool**:
  > we could create an organization, check its ID to see how close
  > we were to the target ID, and then immediately delete it."
- **Description:** The attacker first considers automating
  organization creation via the GitHub Enterprise admin API as the
  bot-creation primitive — orgs share the user ID pool, so an
  attacker-controlled org with the right ID would pass the filter.
  Wiz rejects this path because GitHub organization accounts
  cannot open pull requests, but instead of discarding the API
  entirely they retain it as an *ID-sampling probe*: each
  create-org-then-immediately-delete-org call returns the next
  allocated GitHub ID, giving the attacker live observability of
  the global ID allocation pointer. This sampling capability is
  the load-bearing input for the race-timing in step 7.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 4; an attacker-controlled
  GitHub Enterprise tenancy (or equivalent API surface) able to
  create and delete organizations programmatically.
- **Postconditions:** The attacker has (a) abandoned org creation
  as the bot-creation primitive (orgs cannot open PRs, per the
  blog's explicit reason) and pivoted to the GitHub Apps manifest
  flow in step 6, and (b) retained org creation as a probe for
  live GitHub ID readings that will drive the burst timing in
  step 7.
- **Outputs:** `[PROPOSED: github_user_id]` (each probe call
  yields the current high-water-mark ID).
- **Reproducibility:** `demonstration_only`.
- **Why this tier (not a higher one):** Reproducing the
  enterprise-org-creation API surface in a lab requires a real
  GitHub Enterprise tenancy, and the *sampling* meaning of the
  technique depends on real GitHub's real ID allocation pointer
  moving in real time — substitution by a documentation
  demonstration is honest.

### Chain step 6: Successful approach — atomic bot creation via GitHub Apps manifest flow

- **Blog excerpt:**
  > "The real breakthrough came from GitHub Apps. Creating an app
  > generates a corresponding bot user (e.g. app-name[bot]) that
  > _can_ interact with pull requests. It's also possible to
  > automate app creation via the manifest flow. While it's
  > composed of a few steps, it can be made **atomic**: the app and
  > its bot are only created when a final confirmation URL is
  > visited."
- **Description:** The attacker scripts GitHub App manifest-flow
  registrations such that each results in a pre-built confirmation
  URL; visiting the URL atomically creates the app and its
  `[bot]` user, which gets the next-available GitHub ID and is
  permitted to interact with pull requests.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of steps 4 and 5; a controllable GitHub
  account that can drive the manifest flow at scale.
- **Postconditions:** ~200 pre-staged confirmation URLs, each of
  which will atomically allocate a new bot user with the
  next-available numeric ID when visited.
- **Outputs:** `[PROPOSED: github_app_manifest]`,
  `[PROPOSED: github_user_id]` (the bot user ID once captured).
- **Reproducibility:** `partial_simulation`.
- **Why this tier (not a higher one):** The manifest flow can be
  exercised against a real GitHub account in a lab; the racing
  semantics that make the URL-burst meaningful depend on real
  GitHub's real ID allocation and cannot be reproduced offline.

### Chain step 7: ID capture by flooding the manifest-confirmation endpoint

- **Blog excerpt:**
  > "We waited until the live ID count was just ~100 IDs away from
  > the target ID, and then visited all 200 URLs at once,
  > triggering a flood of new bot user registrations. The target
  > ID was 226755743, which contained a trusted maintainer ID."
- **Description:** The attacker monitors the live GitHub ID
  allocation pointer until it is within ~100 of the target ID
  (`226755743`, which contains the substring of a trusted
  maintainer ID), then visits all ~200 pre-staged confirmation
  URLs in a burst — one of the resulting bot users gets the target
  ID.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Outputs of steps 4 and 6; the live ID
  allocation pointer is within burst distance of the target.
- **Postconditions:** The attacker controls a `[bot]` user whose
  numeric ID satisfies the `aws-sdk-js-v3` CodeBuild project's
  `ACTOR_ID` filter.
- **Outputs:** `[PROPOSED: github_user_id]` (`226755743`).
- **Reproducibility:** `demonstration_only`.
- **Why this tier (not a higher one):** The capture mechanic
  depends on real GitHub at real time-scales; the technique can be
  illustrated against a recorded historical sequence but cannot be
  reproduced live in a self-contained lab.

### Chain step 8: PR submission triggers a privileged CodeBuild build

- **Blog excerpt:**
  > "We submitted the PR, and soon after received a notification:
  > a build had been triggered. Moments later, we had successfully
  > obtained the GitHub credentials of the `aws-sdk-js-v3`
  > CodeBuild project."
- **Description:** From the captured bot user, the attacker opens
  a pull request against `aws/aws-sdk-js-v3`; the CodeBuild
  project's webhook filter accepts the PR (the bot's ID matches
  the unanchored regex), and the project's normal build runs with
  its normal credentials.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 7.
- **Postconditions:** A CodeBuild build is running with privileged
  credentials, executing PR-supplied code.
- **Outputs:** `[PROPOSED: aws_codebuild_build_id]` (a build
  reference).
- **Reproducibility:** `partial_simulation` against a lab CodeBuild
  project configured analogously.
- **Why this tier (not a higher one):** A lab can reproduce a
  PR-triggered build against a lab CodeBuild project, but the
  specific filter bypass against the real AWS project is closed
  (AWS anchored the filter on 2025-08-27).

### Chain step 9: Memory-dump the build container to extract the GitHub PAT

- **Blog excerpt:**
  > "Our payload retrieved the GH token by dumping the memory of a
  > process within the build environment."
- **Description:** An attacker-supplied build payload dumps the
  memory of a specific process inside the CodeBuild build
  container and locates the GitHub Personal Access Token in
  memory. The blog does not name the dumped process, does not
  describe the dump primitive used (no procfs path, ptrace, gdb,
  gcore, or custom binary is named), and gives only the single
  sentence quoted above as technical detail. Wiz notes that "a
  previous memory dump mitigation in CodeBuild, which AWS
  implemented in response to the Amazon Q incident, overlooked
  this particular process."
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 8; build container process
  privilege sufficient to read the target process's memory
  (achieved within the standard CodeBuild execution environment as
  of the disclosure).
- **Postconditions:** The attacker holds the GitHub Classic PAT for
  `aws-sdk-js-automation`.
- **Outputs:** `[PROPOSED: github_pat]`.
- **Reproducibility:** `partial_simulation`.
- **Why this tier (not a higher one):** The general
  process-memory-dump technique is reproducible against a lab
  CodeBuild project with a target process holding fake credentials;
  the specific bypass against AWS-managed CodeBuild is closed by
  AWS's September 2025 memory-dump hardening.

### Chain step 10: Identify the captured PAT and its scopes

- **Blog excerpt:**
  > "The credentials we obtained were a GitHub Classic Personal
  > Access Token (PAT) belonging to the `aws-sdk-js-automation`
  > user."
- **Description:** The attacker inspects the captured PAT to learn
  its owning user, token class, and OAuth scopes (`repo` and
  `admin:repo_hook` per the blog). The blog then confirms:
  > "We quickly confirmed that the `aws-sdk-js-automation` user
  > had **full admin privileges** over the repository."
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 9.
- **Postconditions:** The attacker has positive confirmation of
  full repo-admin authority via the captured PAT.
- **Outputs:** `[PROPOSED: github_pat]` (re-typed with known scopes
  + owner), `[PROPOSED: github_repo_reference]` (the repo for
  which admin was confirmed).
- **Reproducibility:** `full` against a lab PAT.
- **Why this tier (not a higher one):** Token introspection is a
  vanilla GitHub API call against `/user` and the token's metadata
  endpoint; reproducible verbatim.

### Chain step 11: Privilege escalation — invite attacker as repo admin

- **Blog excerpt:**
  > "To escalate privileges, we abused the token's `repo` scope,
  > which can manage repository collaborators, and invited our own
  > GitHub user to be a repository administrator."
- **Description:** The attacker uses the captured PAT to invite a
  fresh attacker-controlled GitHub user as a collaborator with
  admin role on the target repository — converting an ephemeral
  PAT-derived capability into a persistent attacker identity that
  no longer depends on the CodeBuild bypass.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Output of step 10.
- **Postconditions:** A persistent attacker-controlled GitHub
  identity holds admin role on the repository; the attacker can
  now push, change branch protections, publish releases, etc.,
  independently of the CodeBuild path.
- **Outputs:** `[PROPOSED: github_repo_reference]` (now with an
  attacker-controlled admin collaborator).
- **Reproducibility:** `full` against a lab repo + lab PAT.
- **Why this tier (not a higher one):** The invitation is a
  vanilla GitHub API call against the collaborators endpoint;
  reproducible verbatim.

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched.
The GitHub-Enterprise-org-creation approach Wiz tried in step 5 is
captured as a *failed-then-superseded variant* within the canonical
chain (it shares no successful steps with the path that worked), not
as a separate alternative path. Per `schema.md §4.8` lines 387–401,
`alternative_paths` is reserved for cases where the blog presents two
or more *working* attack paths (e.g., the B2B-trust-hopping example
in the schema); this blog presents one working path plus one
documented dead end.

## 6. Facets

Per `schema.md §4.13`. The Phase-0 bundled facets registry currently
ships only `target:aws`; every other facet listed below is marked
`[PROPOSED: <name>]` per ground rule 2.

### 6.1 `target:*` (Extractor-derived)

- `target:aws` — exists in `registry/facets.yaml` (the sole Phase-0
  seed entry).
- `[PROPOSED: target:aws_codebuild]` — the attack targets AWS
  CodeBuild specifically (not arbitrary AWS services); justifies a
  dedicated facet because CodeBuild-specific defenses (PR
  comment-approval gates, CodeBuild-hosted runners, webhook filter
  anchoring) only make sense scoped to this service.
- `[PROPOSED: target:github]` — half the attack surface is GitHub:
  Apps manifest flow, sequential ID allocation, PAT scopes, repo
  admin invitations.
- `[PROPOSED: target:github_apps]` — the App-bot creation path is
  load-bearing; abstracting it under `target:github` would obscure
  the specific primitive (atomic bot user creation at
  confirmation-URL visit).

### 6.2 `runtime:*` (Planner-derived; walker's guess)

- `[PROPOSED: runtime:aws]` — `schema.md §4.13` lists this as a v1
  first-class runtime but it is not yet present in
  `registry/facets.yaml`'s single seed entry.
- `[PROPOSED: runtime:github]` — same status as above; the lab
  needs both AWS and GitHub provisioning.

A lab is therefore multi-platform (count ≥ 2 of `runtime:*`),
matching the §14 `multi_platform` coverage tag.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

- `[PROPOSED: lab_class_signal:vulnerability_chain]` — blog-derived;
  the lab teaches a specific bug chain (regex anchoring +
  sequential ID allocation + atomic App-bot creation).
- `[PROPOSED: lab_class_signal:requires_infra]` — lab-derived; the
  lab cannot be exercised on a single laptop without provisioning
  a real CodeBuild project and a real GitHub repository.
- `[PROPOSED: lab_class_signal:external_channel]` — blog-derived;
  the ID race happens off the AWS surface entirely, against
  GitHub's allocation pointer.
- `[PROPOSED: lab_class_signal:waits_for_condition]` — blog-derived;
  the "eclipse" window is the canonical
  wait-for-external-condition pattern (`schema.md §4.13`'s
  definition).
- `[PROPOSED: lab_class_signal:expected_detections]` — blog-derived;
  the blog describes AWS's post-disclosure
  detection/mitigation additions (memory-dump hardening for build
  processes containing GitHub tokens).

## 7. Value types referenced

The Phase-0 `registry/value_types.yaml` ships only `aws_credentials`.
This blog's chain does *not* surface `aws_credentials` directly —
the stolen secret is a *GitHub* PAT, which the AWS-side build
container happened to hold. Every other typed value below is marked
`[PROPOSED]` per ground rule 2.

- `aws_credentials` (exists) — referenced for contrast only; not an
  output of any chain step here. Worth noting because a naïve
  Extractor might tag the CodeBuild credential exfiltration as
  `aws_credentials`, which would be a mis-attribution: AWS
  CodeBuild's build environment held a *GitHub* PAT, not an AWS IAM
  credential.
- `[PROPOSED: github_pat]` — Classic Personal Access Token,
  long-lived, owner `aws-sdk-js-automation`, scopes `repo` and
  `admin:repo_hook`. The exfiltrated credential.
- `[PROPOSED: github_user_id]` — numeric GitHub ID, the entire
  pivot of the attack; the blog explicitly states user IDs are
  sequential and that org IDs share the user pool; App-bot IDs
  appear to allocate from the same sequence (walker inference
  from the race mechanic's feasibility, see §3
  vulnerability_story note).
- `[PROPOSED: github_repo_reference]` — `owner/name`-shaped
  reference (e.g., `aws/aws-sdk-js-v3`).
- `[PROPOSED: github_app_manifest]` — the manifest-flow confirmation
  URL that atomically creates an App + its `[bot]` user.
- `[PROPOSED: github_webhook_filter]` — CodeBuild's webhook filter
  expression including the unanchored regex pattern.
- `[PROPOSED: aws_codebuild_project_reference]` — reference to a
  CodeBuild project by name + account/region.
- `[PROPOSED: aws_codebuild_build_id]` — reference to a specific
  build run within a project, output of the PR-triggered build.

## 8. Defender techniques

Not applicable for this blog — Wiz disclosed a vulnerability rather
than reconstructing an incident; no investigation, threat-hunting,
detection-engineering, or forensic-analysis methodology is described
in the source. (The blog *does* describe AWS's *mitigations* — those
go in §9 as defenses, not §8.)

## 9. Defenses

Five defense entries derived from Wiz's "Recommendations" section
and the disclosure-timeline mitigations.

- **Description:** Pull Request Comment Approval — require an
  approving comment from a trusted maintainer before a PR's build
  runs.
  **Applicability:** `customer_actionable`.
  **Addresses chain steps:** 8, 9.
  **Detection path / format:** Not applicable; this is a build-gate
  control, not a detection.

- **Description:** Use CodeBuild-hosted runners to manage build
  triggers via GitHub workflows rather than CodeBuild's
  webhook filters.
  **Applicability:** `architectural_mitigation`.
  **Addresses chain steps:** 2, 3, 7, 8.
  **Detection path / format:** Not applicable.

- **Description:** Anchor regex patterns in webhook filters with
  `^` and `$` so the filter matches exactly rather than on
  substring containment. AWS shipped the anchored version on
  2025-08-27.
  **Applicability:** `vendor_only` (for AWS-managed CodeBuild) or
  `customer_actionable` (for customer-authored filters on
  self-managed CodeBuild projects).
  **Addresses chain steps:** 3, 7, 8.
  **Detection path / format:** Not applicable.

- **Description:** Use a unique fine-grained PAT per CodeBuild
  project, scoped to the minimum required permissions, owned by a
  dedicated unprivileged GitHub account — so that a build-time
  credential leak yields a narrowly-scoped token rather than full
  admin over a high-value repo.
  **Applicability:** `customer_actionable`.
  **Addresses chain steps:** 10, 11.
  **Detection path / format:** Not applicable.

- **Description:** Memory-dump hardening: AWS implemented
  "additional hardening to prevent non-privileged builds from
  accessing the project's credentials via memory dumping" in
  September 2025 (extending a partial mitigation that had been
  shipped after the Amazon Q incident).
  **Applicability:** `vendor_only`.
  **Addresses chain steps:** 9.
  **Detection path / format:** Not applicable (this is process
  hardening, not detection).

## 10. External references

- **CVEs cited:** Not stated in blog. The post references AWS
  Security Bulletin 2026-002 but does not assign a CVE.
- **MITRE ATT&CK techniques referenced:** Not stated in blog.
- **External hyperlinks in the blog body** (verbatim URLs, anchor
  context):
  - `https://aws.amazon.com/security/security-bulletins/2026-002-AWS/`
    — AWS Security Advisory for this disclosure ("Read the AWS
    Advisory").
  - `https://aws.amazon.com/security/security-bulletins/AWS-2025-015/`
    — AWS bulletin for the Amazon Q VS Code extension supply-chain
    attack (July 2025) cited as a similar CodeBuild PR-trigger
    compromise.
  - `https://aws.amazon.com/security/security-bulletins/aws-2025-016/`
    — the *prior* CodeBuild memory-dump mitigation AWS shipped
    after the Amazon Q incident, which the blog notes "overlooked
    this particular process."
  - `https://www.wiz.io/blog/s1ngularity-supply-chain-attack` —
    Wiz's earlier writeup of the Nx S1ngularity supply-chain
    incident, cited as related context.
  - `https://github.com/aws/aws-sdk-js-v3/pull/7280` — the actual
    proof-of-concept pull request Wiz submitted ("We submitted the
    PR").
  - `https://docs.aws.amazon.com/codebuild/latest/userguide/pull-request-build-policy.html`
    — AWS CodeBuild documentation for Pull Request Comment
    Approval (referenced in the recommendations section).
  - `https://app.wiz.io/boards/threat-center/wiz-adv-2026-002` —
    Wiz Threat Intel Center pre-built query for customers to
    inspect their own environments.
  - `https://www.csoonline.com/article/4027963/hacker-inserts-destructive-code-in-amazon-q-as-update-goes-live.html`
    — CSO Online coverage of the Amazon Q incident, cited as
    background.
  - `https://docs.github.com/en/apps/overview` — GitHub Apps
    overview docs (referenced when explaining the App-bot
    primitive).
  - `https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest`
    — GitHub Apps manifest-flow documentation (the load-bearing
    primitive for the atomic bot-creation step 6).

## 11. Real-world incidents

- **Status:** `none_observed`.
- **Evidence source:** Two corroborating statements in the blog.
  (a) Wiz's own statement: "we halted further research and
  immediately reported the issues to AWS" — i.e., the capability
  was demonstrated by the disclosing researchers and not
  exercised maliciously prior to remediation. (b) AWS's audit
  confirmation, quoted verbatim in the blog's AWS Statement
  block: "AWS audited the logs of all public build repositories
  as well as associated CloudTrail logs and determined that no
  other actor had taken advantage of the unanchored regex issue
  demonstrated by the Wiz research team." The vendor-side audit
  confirmation strengthens `none_observed` beyond the
  researcher-side assertion.
- **Incidents:** None to enumerate. (Related context, not the
  same vulnerability: the Amazon Q VS Code extension supply-chain
  attack of July 2025 — AWS bulletin AWS-2025-015 — used a
  related CodeBuild PR-trigger primitive against a different
  AWS-owned project; the blog cites it as analogous tradecraft
  but not as the same vulnerability.)

## 12. Expected lab class

- **Lab kind:** "AWS CodeBuild + GitHub Apps vulnerability-chain
  lab illustrating webhook-filter regex bypass leading to GitHub
  PAT theft and repo-admin supply-chain takeover."
- **Why this class:** The §6 facets converge unambiguously:
  `target:aws_codebuild` + `target:github` + `target:github_apps`
  (the attack surface), `runtime:aws` + `runtime:github` (the
  lab's provisioning targets), and the
  `lab_class_signal:vulnerability_chain` +
  `lab_class_signal:external_channel` +
  `lab_class_signal:waits_for_condition` triple (the lab's
  shape — a bug-driven chain with an off-target dependency on a
  non-deterministic external condition). Multi-platform; complex
  (eleven steps); vulnerability-disclosure-derived;
  supply-chain-shaped in consequence. The Planner should be
  prepared to drop step 7's racing mechanic from real
  reproduction and surface it as `demonstration_only` with a
  meaningful walkthrough rather than attempt to mock GitHub's
  global ID counter.

## 13. Reproducibility (lab-level)

- **Classification (lab-level):** `mixed`.
- **Caveats:** Steps 4, 5, 7 are `demonstration_only` (real
  GitHub-ID racing depends on real GitHub at real time-scales).
  Steps 1, 6, 8, 9 are `partial_simulation` (the techniques are
  reproducible against a lab CodeBuild project + a lab GitHub
  account, but not against the specific AWS-managed surface,
  which is patched). Steps 2, 3, 10, 11 are `full` (mechanical
  configuration reading or vanilla GitHub API calls,
  reproducible verbatim).
- **Derivation trace:** Per the any-heterogeneity-mixed rule in
  `schema.md §4.8` line 438: required steps span three tiers
  (`full`, `partial_simulation`, `demonstration_only`), so the
  lab is `mixed`. Per-step tiers: step 1 →
  `partial_simulation`; step 2 → `full`; step 3 → `full`;
  step 4 → `demonstration_only`; step 5 →
  `demonstration_only`; step 6 → `partial_simulation`;
  step 7 → `demonstration_only`; step 8 →
  `partial_simulation`; step 9 → `partial_simulation`;
  step 10 → `full`; step 11 → `full`.
- **Overall assessment:** A lab can teach the bug primitives
  (regex anchoring, atomic App-bot creation, build-container
  memory dumping, PAT-scope privilege escalation) with high
  fidelity, but cannot reproduce the temporal racing dynamic
  that makes the end-to-end chain operationally feasible on
  real GitHub. The honest framing for the learner is "the
  technique is reproducible at each link; the chain's
  feasibility depends on a non-deterministic GitHub-side
  condition that the lab demonstrates rather than races."

## 14. Coverage tags

Per `eval.md §7.3`, drives `coverage_tags:` in
`eval/blog-sets/manifest.yaml`:

- `cloud:aws`
- `platform:github`
- `multi_platform`
- `complexity:complex` (11 chain steps, exceeds the 9+ threshold)
- `thesis:vulnerability_chain`
- `thesis:supply_chain_compromise`
- `thesis:cloud_provider_flaw`
- `thesis:ttp_chain`
- `thesis:privilege_escalation`
- `lab_class_signal:vulnerability_chain`
- `lab_class_signal:requires_infra`
- `lab_class_signal:external_channel`
- `lab_class_signal:waits_for_condition`
- `lab_class_signal:expected_detections`
- `vulnerability_disclosure`

(`non_first_class_runtime` is *not* applied — both `runtime:aws` and
`runtime:github` are listed as first-class in `schema.md §4.13`.)

## 15. Manual ground-truth notes

**Drafted by:** Claude Opus 4.7, 2026-05-20. Verified by: [pending
human review].

### Shape-classification call

None of the Phase-0 manifest shapes (`aws_ttp`, `supply_chain`,
`incident_analysis`) fits cleanly. The blog is a vendor
vulnerability disclosure of an AWS-managed CodeBuild flaw whose
*consequence* is supply-chain-shaped (admin takeover of an
npm-published AWS-owned repo). It is *not* `aws_ttp` (the heart is
a CodeBuild misconfig and a GitHub ID-reuse race, not
customer-side AWS tradecraft), and it is *not* `incident_analysis`
(no real-world incident; Wiz halted after credential capture). I
picked `supply_chain` as the least-bad fit because the lab the
Planner would build is shaped like supply-chain compromise, but I
flag `aws_vuln_disclosure` (per the brief's example) as a
plausible new manifest shape worth considering once the curated
set grows. The walker's call: surface this in §15 rather than
silently bend the existing taxonomy. Recorded for ADR
consideration.

### Ambiguous wording in the blog

- "**Two missing characters**" is friendly vendor framing for the
  missing `^` and `$` regex anchors. The phrasing risks being
  copied as a chain-step description by an LLM Extractor, losing
  the technical precision (anchoring semantics, regex-engine
  `match` vs. `fullmatch`). I rewrote the relevant chain-step
  description (step 3) to spell out the anchor + substring
  semantics explicitly.
- "**Soon after received a notification: a build had been
  triggered. Moments later, we had successfully obtained the
  GitHub credentials**" (step 8) collapses two distinct events
  (build start, credential exfiltration) into one passive-voice
  sentence. I split them into two chain steps (steps 8 and 9) to
  preserve the temporal structure and the responsibility
  ownership (the build trigger is a CodeBuild action; the
  credential extraction is the PR-supplied payload's action).
- "**Three private repositories**" and "**one was a personal
  GitHub account of an AWS employee**" — referenced but not named.
  An Extractor will mark these as `unknown_from_blog` correctly;
  the walker should not invent identities.

### Alternative readings rejected

- The blog could be read as `cloud_provider_flaw`-only (AWS owned
  the bug; AWS fixed the bug). I rejected this in favor of a
  five-type thesis list because the chain unambiguously also
  exercises `ttp_chain` (an end-to-end attacker sequence),
  `vulnerability_chain` (a specific named flaw),
  `supply_chain_compromise` (the lab-class outcome), and
  `privilege_escalation` (PAT → repo admin via `repo` scope). The
  brief's ground rule 3 explicitly invites multi-typing; I
  applied it generously.
- Step 4 (eclipse window) and step 7 (ID-flood capture) are
  arguably `not_reproducible` rather than `demonstration_only` —
  they depend on real GitHub's real allocation pointer, and a
  lab cannot recreate that. I picked `demonstration_only`
  because the math (step 4) and the burst-visit technique (step
  7) can each be illustrated meaningfully in a lab context per
  `schema.md §4.20`'s "demonstration-must-be-meaningful" floor.
  A more conservative reading would drop both steps to
  `not_reproducible` and exclude them entirely from the chain;
  flagging the alternative for the verifier.

### Calls the walker made

- **Facet `target:github_apps` as distinct from `target:github`.**
  I created a finer-grained facet because the App-manifest
  atomic-bot-creation primitive is what makes the racing
  operationally feasible — it is the load-bearing GitHub
  surface here, not GitHub-the-VCS. A Planner-only `target:github`
  facet would obscure this. The walker prefers explicitness;
  a reviewer might collapse to `target:github` alone.
- **Value type `github_user_id`.** I treated GitHub's numeric ID
  as a first-class value type (not a string) because (a) it is
  the literal substring-matched object in step 3 and (b) its
  sequential allocation semantics are what make step 7
  operationally feasible. Some Extractors might collapse this
  to `sensitive_string` or omit it; I think that is wrong here.
- **Process name in step 9.** Wiz writes "a process within the
  build environment" without naming it ("AWS implemented memory
  dump mitigation in response to the Amazon Q incident,
  overlooked this particular process"). I did not infer a
  specific process name; chain step 9 says "process name not
  disclosed."
- **CVE assignment in §10.** No CVE was assigned; the blog only
  cites AWS bulletin 2026-002. I left CVE as "Not stated in
  blog" per ground rule 8. An LLM Extractor is at high risk of
  hallucinating a CVE from training-data analogies to similar
  CodeBuild or supply-chain issues; this should be a Phase-1
  hard rejection.

### Failure modes for an LLM Extractor

- **"Eclipse" terminology.** Wiz's coinage is cute and
  non-standard. An Extractor may either (a) ignore it and lose
  the temporal-window concept, or (b) over-elevate it to a
  thesis-type or facet name. The right treatment is to preserve
  the vendor term in `tradecraft_notes` for cross-blog
  matching (per `schema.md §4.7`'s soft naming convention,
  prefixed by the primary target facet — e.g.,
  `github:eclipse-window-racing`), not promote it to schema.
- **Interleaved narrative and retrospective analysis.** The blog
  alternates between attacker-perspective narrative ("We waited
  until the live ID count was just ~100 IDs away…") and
  retrospective analysis ("a regex engine doesn't look for a
  string that perfectly matches the pattern, but one that
  merely _contains_ it"). Distinguishing the two cleanly is a
  known LLM weakness. Recommend a prompt-segmentation step in
  the Phase-1 Extractor that tags each paragraph by mode
  (narrative / analysis / mitigation / context) before
  extracting chain steps from narrative paragraphs only.
- **Passive-voice credential ownership.** "The credentials we
  obtained were a GitHub Classic PAT belonging to the
  `aws-sdk-js-automation` user" — easy for an Extractor to
  mis-attribute *who holds the token at each moment* (the
  CodeBuild build process? Wiz's exfil channel? AWS's
  pre-revocation state?). The chain-step boundary I drew at
  step 9 (memory dump) → step 10 (PAT identification) makes
  the ownership transition explicit; an Extractor lacking that
  structural guidance may collapse steps 9 and 10 and lose
  the distinction.
- **AWS-side mitigation chronology as a chain.** AWS shipped
  three mitigations in sequence (Aug 27 filter anchoring, Sept
  memory-dump hardening, plus PAT revocation). An Extractor
  could be tempted to treat the chronology as a defender-side
  TTP chain — it is not. These are vendor remediations and
  belong in §9 defenses, not §8 defender techniques.

### Phase-1 prompt-engineering hints

- The Extractor's prompt should include an explicit "do not
  invent CVEs" gate, citing the blog's CVE-silence as the
  correct ground-truth output. CodeBuild-shaped disclosures
  often have NVD-cited analogues; the gate has to fire
  *before* any retrieval pass that might surface them.
- The prompt should include an example pattern for
  "failed-attempt-then-pivot" within a single chain (step 5 →
  step 6 here), distinct from `alternative_paths` (which is
  reserved for multiple *working* paths).
- The reproducibility-tier prompt should include the eclipse /
  ID-racing pattern as a worked example of when
  `demonstration_only` is correct and `partial_simulation`
  would be dishonest (because the simulation can't reproduce
  the load-bearing real-world condition).

### Disclosure timeline (preserved as `extras`-shaped note)

The template has no dedicated slot for disclosure timelines, but
this blog's timeline is pedagogically valuable:

- 2025-08-25 — Wiz reports actor-ID bypass and repo takeover to
  AWS.
- 2025-08-25 — AWS and Wiz meet to review and discuss mitigation.
- 2025-08-27 — AWS anchors the vulnerable actor-ID filters;
  revokes the `aws-sdk-js-automation` PAT.
- 2025-09 (month, day not stated) — AWS implements additional
  hardening to prevent non-privileged builds from accessing
  the project's credentials via memory dumping.
- 2026-01-15 — Public disclosure.

This belongs in the Manifest's `extras` block (per `schema.md
§4.10`) — recorded here so a future Planner does not lose it.
