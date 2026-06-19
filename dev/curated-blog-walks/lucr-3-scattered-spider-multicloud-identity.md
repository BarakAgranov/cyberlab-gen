# Blog walk: `lucr-3-scattered-spider-multicloud-identity`

> **PROVISIONAL — AGENT-DRAFTED.** This walk was drafted by an agent from the
> Permiso p0 Labs source (`dev/.task10-research/multi-cloud-lucr3.json`,
> originally via WebFetch). It ships **provisional, pending a human
> ground-truth pass**. No eval calibration value may be locked against an
> unreviewed walk (ADR 0102, `eval.md §7.2`): until a human reads the source
> end-to-end and signs off, treat every classification, facet, and
> reproducibility tier below as a draft hypothesis, not a gold-standard
> reference.

A manual ground-truth reading of a curated blog. The walker reads the blog
end-to-end and maps its narrative onto the AttackSpec structure
(`docs/schema.md §4.8`). This walk is the curated set's **required multi-cloud
example**: the compromised identity provider (Okta / Azure AD / Ping) is the
cross-cloud hub, and the canonical chain deliberately fans out from that one
identity into **AWS + Azure (M365 / Entra) + GCP (Google Workspace)** so the
multi-cloud span is genuine, not incidental. It is also an `incident_analysis`
blog with a substantive defender story (the 27 detection rules in §8) and
**mixed** lab-level reproducibility.

---

## 1. Header

- **id:** `lucr-3-scattered-spider-multicloud-identity` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `incident_analysis`
- **URL:** https://permiso.io/blog/lucr-3-scattered-spider-getting-saas-y-in-the-cloud
- **Canonical URL:** https://permiso.io/blog/lucr-3-scattered-spider-getting-saas-y-in-the-cloud
- **Title:** "LUCR-3: Scattered Spider Getting SaaS-y in the Cloud"
- **Publisher:** Permiso Security (p0 Labs)
- **Publisher kind:** `vendor_lab`
- **Authors:** Permiso p0 Labs (Ian Ahl / p0 Labs team, per byline)
- **Publication date:** 2023-10
- **Accessed date:** 2026-06-19 (agent draft)
- **Content hash:** TBD (the Extractor produces this mechanically)

## 2. One-paragraph summary

Permiso's p0 Labs profiles LUCR-3 — the cluster that overlaps Scattered Spider,
Roasted 0ktapus, and Mandiant's UNC3944 — a financially-motivated actor whose
defining move is to compromise an **identity provider** (Okta, Azure AD / Entra,
or Ping) and then pivot into whichever clouds the victim federates from that
IDP. Because the IDP is the trust anchor for AWS, Azure / M365, and Google
Workspace at once, a single social-engineered or SIM-swapped identity fans out
across **multiple clouds**. The actor barely uses malware: it lives off the
victim's own consoles and SaaS apps, registers its own MFA device for
persistence, scrapes credentials from AWS Secrets Manager via CloudShell,
disables CloudTrail and GuardDuty, and exfiltrates source code, customer data,
and code-signing certificates from S3 / RDS / DynamoDB and GitHub / GitLab —
then extorts the victim (some personas deploying ALPHV / BlackCat ransomware).
The write-up is a broad threat-actor *profile*, not a single linear intrusion;
this walk condenses it into one representative canonical chain.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types:** `ttp_chain`, `incident_analysis`, `persistence_pattern`,
  `privilege_escalation`, `cross_tenant_compromise`.
- **Summary:** A multi-cloud, identity-centric campaign: compromise the IDP,
  bypass MFA, register an attacker-controlled MFA factor for persistence, then
  fan out from the IDP into AWS + Azure / M365 + Google Workspace, escalate via
  IAM policy manipulation, harvest secrets, disable cloud logging, and
  exfiltrate data for extortion.
- **Attacker objective:** Steal IP, source code, customer data, and
  code-signing certificates across the victim's clouds and extort the victim
  (demands often in the tens of millions USD); some personas deploy
  ALPHV / BlackCat ransomware as a finisher.
- **Vulnerability story:** **Architectural, not a CVE.** There is no single
  vulnerability disclosure here. The "vulnerability" is a design property: a
  **federated IDP that trusts SMS / push MFA and bridges multiple clouds**
  means one social-engineered or SIM-swapped identity yields AWS + Azure +
  Google Workspace simultaneously. Living-off-the-land via native consoles
  evades malware detection; residential VPNs defeat impossible-travel
  heuristics; mailbox-rule and notification cleanup defeats notification-based
  detection. This is flagged for the Extractor in §15 — there is no CVE to
  anchor on, and an Extractor keyed on CVE strings will leave this field thin.

## 4. Chain steps

The ordered list of attacker actions, mapped from the source profile into a
representative canonical chain. Step shape mirrors `schema.md §4.8` lines
358–383. Each step records the **cloud(s) it touches** so the multi-cloud span
is auditable per step.

### Chain step 1: Identity selection + credential acquisition

- **Blog excerpt:**
  > They ensure they are targeting users that will have the access they need to carry out their mission.
- **Description:** Identify high-privilege identities — admins, developers,
  security staff — and obtain their credentials from deep-web markets or via
  social engineering / phishing.
- **MITRE techniques:** `T1589` (Gather Victim Identity Information),
  `T1598` (Phishing for Information)
- **Clouds touched:** Okta / Azure AD (Entra) / Ping (the IDP layer — pre-cloud-fan-out)
- **Preconditions:** A victim org federating one or more clouds from a single IDP.
- **Postconditions:** Attacker holds primary credentials for a high-privilege identity.
- **Outputs:** `okta_session_token` (proposed), `entra_refresh_token` (proposed)
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** Targeting logic and credential sourcing
  can be *simulated* in a lab (seed a "leaked" credential), but real
  compromised credentials from a deep-web market cannot be ethically obtained,
  so this is not `full`.

### Chain step 2: MFA bypass (SIM swap / push fatigue / OTP replay)

- **Blog excerpt:**
  > SIM Swapping (when SMS OTP is enabled) ... Push Fatigue (when SMS OTP is not enabled).
- **Description:** Defeat the second factor: SIM swap when SMS OTP is enabled,
  push fatigue (MFA bombing) when it is not, OTP capture/replay via an
  adversary-in-the-middle proxy, or insider social engineering of the help desk.
- **MITRE techniques:** `T1111` (Multi-Factor Authentication Interception),
  `T1556` (Modify Authentication Process)
- **Clouds touched:** Okta / Azure AD (Entra) / Ping (the IDP layer)
- **Preconditions:** Primary credentials (step 1); knowledge of the victim's MFA modality.
- **Postconditions:** An authenticated IDP session in the victim's identity plane.
- **Outputs:** `okta_session_token` (proposed) / `entra_refresh_token` (proposed)
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** Push-fatigue and phishing-redirect
  mechanics are simulatable against a lab IDP tenant, but **SIM swapping
  requires real telco infrastructure** that a lab cannot stand up safely, so
  the step as a whole is `partial_simulation`, not `full`.

### Chain step 3: Register attacker MFA device + alternate factors (persistence)

- **Blog excerpt:**
  > When a user register a device that is in a different ecosystem than their previous device (Android to Apple as an example).
- **Description:** Register an attacker-controlled device or email as a trusted
  authenticator and modify MFA settings (e.g., enable SMS OTP in Azure AD) so
  the foothold survives a password reset. This is the persistence anchor on the
  identity plane.
- **MITRE techniques:** `T1098` (Account Manipulation), `T1556` (Modify Authentication Process)
- **Clouds touched:** Azure AD (Entra), Okta (the IDP layer)
- **Preconditions:** An authenticated IDP session (step 2).
- **Postconditions:** Attacker holds an independent, durable MFA factor on the victim identity.
- **Outputs:** persistent IDP authenticator binding (no distinct value-type key)
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Fully reproducible in a sandbox IDP
  tenant — provision a test identity, modify its MFA configuration, register an
  external device. No real telco or human factor is needed.

### Chain step 4: SaaS reconnaissance across M365 + Google Workspace

- **Blog excerpt:**
  > Searching through and viewing documents in the various SaaS applications like SharePoint, OneDrive, knowledge applications, ticketing solutions, and chat applications.
- **Description:** Use the legitimate SaaS interfaces the IDP unlocks to search
  for credentials, deployment and code-signing procedures, and other sensitive
  data — mapping the environment across **Azure / M365** (SharePoint, OneDrive)
  and **Google Workspace** before touching any cloud control plane.
- **MITRE techniques:** `T1526` (Cloud Service Discovery), `T1087` (Account Discovery)
- **Clouds touched:** **Azure (M365)** + **GCP (Google Workspace)** — the first cross-cloud fan-out.
- **Preconditions:** IDP session that federates M365 and Google Workspace (steps 2–3).
- **Postconditions:** An inventory of secrets locations, runbooks, and target data across two clouds.
- **Outputs:** discovered-credential leads (feed steps 5–6)
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Sandbox M365 and Google Workspace
  tenants populated with mock documents reproduce this search-and-view behavior
  exactly; nothing about it is unsafe or non-deterministic.

### Chain step 5: AWS console recon + privilege escalation (IAM policy manipulation)

- **Blog excerpt:**
  > LUCR-3 has been seen modifying the policy of existing roles assigned to EC2 instances (ReplaceIamInstanceProfileAssociation).
- **Description:** Pivot from the IDP into **AWS**: browse the console,
  inventory EC2 via Systems Manager, then escalate by replacing IAM instance
  profiles (`ReplaceIamInstanceProfileAssociation`) or updating login profiles
  to assume more privileged roles.
- **MITRE techniques:** `T1580` (Cloud Infrastructure Discovery),
  `T1548` (Abuse Elevation Control Mechanism), `T1526` (Cloud Service Discovery)
- **Clouds touched:** **AWS** — the AWS arm of the fan-out.
- **Preconditions:** A federated AWS session via the IDP (steps 2–3).
- **Postconditions:** Elevated AWS privileges (a more powerful role/instance profile).
- **Outputs:** `aws_credentials`, `aws_iam_role_arn`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** IAM policy manipulation,
  `UpdateLoginProfile`, instance-profile replacement, and console recon are all
  fully reproducible against a sandbox AWS account.

### Chain step 6: Secrets harvesting via AWS CloudShell

- **Blog excerpt:**
  > LUCR-3 will leverage AWS CloudShell to scrape all credentials.
- **Description:** Use AWS CloudShell to dump AWS Secrets Manager (and where
  present, Terraform / Vault) credentials — database passwords, API keys, and
  partner credentials.
- **MITRE techniques:** `T1552.007` (Unsecured Credentials: Container API),
  `T1555` (Credentials from Password Stores)
- **Clouds touched:** **AWS**
- **Preconditions:** Elevated AWS access (step 5); Secrets Manager / CloudShell reachable.
- **Postconditions:** A bag of harvested secrets (DB, API, partner credentials).
- **Outputs:** `aws_secretsmanager_secret` (proposed), `aws_credentials`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Populate Secrets Manager with mock
  secrets and reproduce the CloudShell scrape verbatim in a sandbox account.

### Chain step 7: Cloud persistence (IAM users / access keys / login profiles)

- **Blog excerpt:**
  > LUCR-3 will attempt to create IAM Users when available. They choose names that align with the victim identity.
- **Description:** Create victim-aligned IAM users and access keys (names that
  blend in with legitimate identities), or co-opt existing login profiles, for
  durable programmatic access independent of the IDP foothold.
- **MITRE techniques:** `T1098` (Account Manipulation), `T1136` (Create Account),
  `T1098.001` (Additional Cloud Credentials)
- **Clouds touched:** **AWS** (primary); **Azure AD / Entra** (analogous identity persistence)
- **Preconditions:** Elevated cloud access (step 5).
- **Postconditions:** Durable long-lived programmatic credentials.
- **Outputs:** `aws_iam_user` (proposed), `aws_access_key` (proposed)
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Mock IAM users, access keys, and login
  profiles are fully reproducible in a sandbox account.

### Chain step 8: Defense evasion — disable CloudTrail + GuardDuty

- **Blog excerpt:**
  > LUCR-3 also attempts to evade AWS detections by performing DeleteTrail and StopLogging actions.
- **Description:** Blind the environment with `DeleteTrail` / `StopLogging`
  against CloudTrail and by deleting GuardDuty detectors, so subsequent
  exfiltration leaves less audit trail.
- **MITRE techniques:** `T1562.008` (Impair Defenses: Disable Cloud Logs),
  `T1562.001` (Impair Defenses: Disable or Modify Tools)
- **Clouds touched:** **AWS**
- **Preconditions:** Elevated AWS access (step 5).
- **Postconditions:** Reduced/absent CloudTrail + GuardDuty telemetry.
- **Outputs:** world-state change (disabled trail/detector) — a key detection target
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** CloudTrail / GuardDuty disablement is
  fully reproducible in a sandbox and is itself a high-value detection target
  (see §8).

### Chain step 9: Exfiltration — S3 / RDS / DynamoDB + GitHub/GitLab source + certs

- **Blog excerpt:**
  > On the CI/CD side, LUCR-3 will use use the clone, archive, and view raw features of Github and Gitlab to view and download source data.
- **Description:** Using long-lived keys plus tools like S3 Browser, pull data
  from S3 / RDS / DynamoDB; on the CI/CD side, clone / archive / view-raw
  GitHub and GitLab repos to steal source code and code-signing certificates.
- **MITRE techniques:** `T1530` (Data from Cloud Storage Object),
  `T1213` (Data from Information Repositories)
- **Clouds touched:** **AWS** + **GitHub** + **GitLab**
- **Preconditions:** Harvested credentials (step 6) and/or durable keys (step 7); logging blinded (step 8).
- **Postconditions:** Attacker holds stolen source, customer data, and code-signing certs.
- **Outputs:** `github_pat` (proposed), `code_signing_certificate` (proposed),
  exfiltrated data objects
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Sandbox S3 / RDS / DynamoDB plus mock
  repos reproduce the data-theft mechanics exactly.

### Chain step 10: Extortion (and optional ALPHV/BlackCat ransomware)

- **Blog excerpt:**
  > LUCR-3 is a financially motivated threat actor that uses data theft of sensitive data ... to attempt extortion.
- **Description:** Contact the victim with an extortion demand backed by the
  stolen data; some personas escalate by deploying ALPHV / BlackCat ransomware.
- **MITRE techniques:** `T1657` (Financial Theft), `T1486` (Data Encrypted for Impact)
- **Clouds touched:** external (off-cloud comms / endpoint ransomware)
- **Preconditions:** Successful exfiltration (step 9).
- **Postconditions:** Extortion demand delivered; optionally encrypted victim assets.
- **Outputs:** none reproducible in-lab (out-of-band comms / destructive payload)
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** Extortion comms can be *simulated*, but
  deploying real ransomware is not lab-safe; the step is demonstrated, not
  executed, so it is `demonstration_only` rather than `partial_simulation` or `full`.

## 5. Alternative paths (optional)

The source is a threat-actor *profile* that describes the IDP fan-out as a set
of parallel options keyed to which IDP and which clouds a given victim
federates, rather than one rigid sequence. The principal alternative the blog
surfaces is the **MFA-bypass branch in step 2**: SIM swap *when SMS OTP is
enabled* versus push fatigue *when it is not* — two mutually-exclusive entry
techniques selected by the victim's MFA modality, converging on the same
authenticated session. Beyond that, the cloud-side body (steps 5–9) can be
walked against any subset of {AWS, Azure, Google Workspace} a victim federates;
this walk treats the **all-three fan-out as the canonical multi-cloud chain**
because that is this blog's required coverage role (§14), and records the
single-cloud subsets as collapsible variants rather than distinct branches.

## 6. Facets

Per `schema.md §4.13`. Walker's best estimate; tagged with who is
*architecturally* responsible. The multi-cloud span is made explicit here: the
`target:*` and `runtime:*` sets each name **three clouds plus the IDP hub**.

### 6.1 `target:*` (Extractor-derived)

`target:aws`, `target:azure`, `target:entra_id`, `target:gcp`, `target:okta`,
`target:github`, `target:aws_iam`.

The **multi-cloud span is genuine here, not incidental**: `target:aws` +
`target:azure` (with `target:entra_id`) + `target:gcp` are all first-class
targets of the same chain, bridged by `target:okta` (the IDP hub). This is the
property that makes this the curated set's required multi-cloud example.

### 6.2 `runtime:*` (Planner-derived; walker's guess)

`runtime:aws`, `runtime:azure`, `runtime:gcp` (all first-class in v1),
`runtime:github` (first-class). A faithful lab provisions **three cloud
runtimes plus GitHub** so the fan-out is reproduced, not flattened to one cloud
— this is the multi-runtime signal for §12.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

`lab_class_signal:incident_analysis` (it is a defender-perspective profile with
27 detection rules), `lab_class_signal:requires_infra` (multiple cloud tenants),
`lab_class_signal:expected_detections` (the detection rules in §8 are the
deliverable), `lab_class_signal:produces_world_state` (disabled trails, created
IAM users, registered MFA devices), `lab_class_signal:simulated_components`
(the SIM-swap / stolen-cred front is simulated), `lab_class_signal:multi_language`
(AWS CLI/CloudShell + Azure/M365 + GWS + Git tooling span several toolchains).

## 7. Value types referenced

Registry keys (per `schema.md §4.12`) the blog surfaces. Several are **not in
the AWS-only bundled v1 seed registry** and are flagged `(proposed)` — see §15
note 5 and the ADR 0099 §6 caveat (manifest Layer-1 membership is not wired
into `plan`, so these would not be rejected at plan time).

- `aws_credentials` — expected to exist in `registry/value_types.yaml`.
- `okta_session_token` `(proposed)` — IDP session artifact; no AWS-only-seed analog. The IDP-hub primitive.
- `entra_refresh_token` `(proposed)` — Azure AD / Entra refresh token; the cross-cloud-to-Azure primitive.
- `aws_access_key` `(proposed)` — long-lived AWS access key id/secret pair (if not already a seed alias of `aws_credentials`).
- `aws_iam_user` `(proposed)` — created IAM user identity (persistence artifact).
- `aws_secretsmanager_secret` `(proposed)` — a Secrets Manager secret value harvested via CloudShell.
- `github_pat` `(proposed)` — GitHub personal access token / repo-clone credential for source theft.
- `code_signing_certificate` `(proposed)` — a stolen code-signing cert (the high-value extortion target; no AWS-seed analog).

## 8. Defender techniques

Per `schema.md §4.8` lines 403–414. **Substantive for this `incident_analysis`
blog** — the detection content is arguably the source's primary deliverable:
**27 detection rules** spanning AWS / Azure AD / Okta / SaaS (the
`P0_AWS_*`, `P0_AZUREAD_*`, `P0_OKTA_*`, `P0_SAAS_*` rule families).

- **Name:** P0 detection-rule corpus (27 rules across AWS / Azure AD / Okta / SaaS)
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** 1–10 (the rules span the whole lifecycle)
  - **Description:** A published library of detections covering identity abuse,
    cloud control-plane abuse, and SaaS recon — the spine of any lab built from
    this blog.
- **Name:** MFA-ecosystem-switch detection (e.g., Android → Apple)
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** 3
  - **Description:** Flag when a user registers an authenticator in a different
    device ecosystem than their previous one — a high-signal indicator of
    attacker MFA enrollment.
- **Name:** Single-device-multiple-user registration detection
  - **Technique kind:** `threat_hunting`
  - **Applies to chain steps:** 3
  - **Description:** Hunt for one physical device registered against multiple
    user identities (the attacker reusing infrastructure).
- **Name:** CloudTrail `DeleteTrail` / `StopLogging` alerting
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** 8
  - **Description:** Alert on the logging-impairment API calls themselves.
- **Name:** GuardDuty detector-deletion alerting
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** 8
  - **Description:** Alert when a GuardDuty detector is deleted.
- **Name:** Impossible-travel + residential-VPN heuristics
  - **Technique kind:** `threat_hunting`
  - **Applies to chain steps:** 2, 5
  - **Description:** Geo / ASN heuristics tuned to catch the actor's use of
    residential VPNs to defeat naive impossible-travel rules.
- **Name:** Mailbox-rule / OAuth-MFA notification-deletion detection
  - **Technique kind:** `investigation`
  - **Applies to chain steps:** 3, 4
  - **Description:** Detect cleanup of MFA-enrollment and security
    notifications (mailbox rules deleting alerts) used to suppress victim awareness.

## 9. Defenses

Controls per `schema.md §4.8` lines 416–426.

- **Description:** Enforce **phishing-resistant MFA (FIDO2 / WebAuthn)** in
  place of SMS / push factors.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** 2, 3
  - **Detection path / format:** preventive (removes the SIM-swap / push-fatigue surface).
- **Description:** Restrict access to **AWS CloudShell and Secrets Manager**
  (least privilege; deny CloudShell to non-operational roles).
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** 6
- **Description:** Alert on **IAM instance-profile replacement**
  (`ReplaceIamInstanceProfileAssociation`) and `UpdateLoginProfile`.
  - **Applicability:** `detection_only`
  - **Addresses chain steps:** 5
  - **Detection path / format:** CloudTrail event rule.
- **Description:** **Immutable / centralized CloudTrail across the org** so a
  single-account `StopLogging` does not blind the environment.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** 8
- **Description:** **Conditional access on device + geo** at the IDP.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** 2, 5

## 10. External references

- **CVEs cited:** none. The blog describes architectural identity abuse, not a
  CVE-tracked vulnerability (see §3 and §15).
- **Related blogs / clusters:** Okta "0ktapus" / "Roasted 0ktapus" reporting;
  Mandiant **UNC3944**; ALPHV / BlackCat ransomware reporting.
- **MITRE ATT&CK techniques referenced:** the per-step techniques enumerated in
  §4 (T1589, T1598, T1111, T1556, T1098, T1526, T1087, T1580, T1548, T1552.007,
  T1555, T1136, T1098.001, T1562.008, T1562.001, T1530, T1213, T1657, T1486).

## 11. Real-world incidents

Per `schema.md §4.8` lines 339–356.

- **Status:** `incidents_documented`
- **Evidence source:** Permiso p0 Labs observed LUCR-3 activity across many
  client environments; the cluster is corroborated by public reporting under
  the overlapping names Scattered Spider / Roasted 0ktapus / UNC3944.
- **Incidents:**
  - **Name:** 2022–2023 IDP-pivot intrusions (MGM / Caesars-era reporting)
  - **Description:** Identity-provider-led intrusions into large enterprises,
    leading to data theft, extortion, and in some cases ALPHV / BlackCat
    ransomware.
  - **Affected organizations:** multiple enterprises (publicly associated with
    the MGM and Caesars incidents in the same era; Permiso reports broad
    client-base observation).
  - **Attribution:** LUCR-3 (= Scattered Spider / Roasted 0ktapus / UNC3944),
    per Permiso and corroborating vendor reporting.
  - **Date range:** 2022–2023.

## 12. Expected lab class

- **Lab kind:** "Multi-cloud identity-driven incident-analysis lab — an IDP
  foothold (Okta / Entra) fans out to AWS + Azure (M365 / Entra) + GCP (Google
  Workspace), with detection engineering as the primary deliverable."
- **Why this class:** The facets in §6 point unambiguously here. The
  `target:*` / `runtime:*` sets each name **three clouds plus the IDP hub and
  GitHub**, so the Planner must provision **multiple runtimes**
  (`runtime:aws` + `runtime:azure` + `runtime:gcp` + `runtime:github`) rather
  than collapse to one cloud — that is what makes the multi-cloud span real in
  the lab. `lab_class_signal:incident_analysis` +
  `lab_class_signal:expected_detections` mean the lab's center of gravity is the
  27-rule detection corpus (§8), not a single linear exploit;
  `lab_class_signal:simulated_components` reflects the simulated identity-compromise
  front; `lab_class_signal:produces_world_state` reflects the disabled trails,
  created IAM users, and registered MFA devices the detections fire on.

## 13. Reproducibility (lab-level)

Derived from the per-step tiers in §4 via the **any-heterogeneity-mixed rule**
(`schema.md §4.8` line 438): if all required steps share a tier, take that tier;
if required steps span tiers, the lab is `mixed`.

- **Classification (lab-level):** `mixed`
- **Caveats:** The chain is heterogeneous by design. The **cloud-abuse body
  (steps 3–9) is `full`** — every cloud-side action (attacker MFA registration,
  SaaS recon, IAM escalation, CloudShell secrets scrape, IAM-user persistence,
  CloudTrail/GuardDuty disablement, S3/DB/repo exfiltration) is fully scriptable
  in sandbox tenants. The **identity-compromise front (steps 1–2: SIM swap /
  real stolen credentials) is `partial_simulation`** because SIM swapping needs
  real telco infrastructure and genuine stolen credentials cannot be ethically
  sourced. The **ransomware/extortion tail (step 10) is `demonstration_only`**
  because deploying real ransomware is not lab-safe.
- **Derivation trace:** steps 1, 2 = `partial_simulation`; steps 3, 4, 5, 6, 7,
  8, 9 = `full`; step 10 = `demonstration_only`. The required steps therefore
  span **three distinct tiers** (≥ 2 ⇒ heterogeneous) → **`mixed`**.
- **Overall assessment:** A lab can faithfully reproduce the multi-cloud
  living-off-the-land cloud-abuse chain and its detections in full; it must
  *simulate* the human-factor initial access and *stub* the ransomware tail.

## 14. Coverage tags

Drives `coverage_tags:` in `eval/blog-sets/manifest.yaml`. This blog's coverage
role is **multi-cloud breadth + incident-analysis depth**.

`cloud:aws`, `cloud:azure`, `cloud:gcp`, `multi_cloud`, `multi_platform`,
`incident_analysis`, `mixed_reproducibility`, `platform:okta`,
`platform:github`, `complexity:complex`, `thesis:ttp_chain`,
`lab_class_signal:expected_detections`, `identity_provider`.

The first eight tags are the task-mandated set: `cloud:aws`, `cloud:azure`,
`cloud:gcp` (the three-cloud span), `multi_cloud` + `multi_platform` (it crosses
clouds *and* SaaS/CI platforms), `incident_analysis` (defender techniques
present, §8), `mixed_reproducibility` (§13), and `platform:okta` (the IDP hub).
`complexity:complex` reflects the 10-step chain (9+ ⇒ complex).

## 15. Manual ground-truth notes

**The highest-value section** — what a human reading this source noticed that an
LLM Extractor on the same blog is likely to miss.

- **The blog is a profile, not one intrusion (the dominant ambiguity).** The
  source describes LUCR-3's *repertoire* across many victims, not a single
  linear kill chain. This walk's call: **condense the repertoire into one
  representative canonical chain** that exercises the full IDP-to-multi-cloud
  fan-out. An alternative reading — emitting several short disjoint chains, one
  per cloud — is defensible but loses the cross-cloud `depends_on` edges (the
  IDP session in steps 1–3 is the shared precondition for the AWS, Azure, and
  GWS arms). The Extractor should be prompted to recognize profile-shaped blogs
  and synthesize a canonical chain rather than emit fragments.

- **Multi-cloud is the defining property, and it is easy to flatten.** The
  IDP is the cross-cloud hub: steps 1–3 are IDP-plane, step 4 is Azure(M365) +
  GCP(Google Workspace), steps 5–9 are AWS (+ GitHub/GitLab). A naive Extractor
  that fixates on the AWS CloudShell/IAM material (the most concrete, most
  quotable section) will emit an **AWS-only** chain and silently drop the Azure
  and Google Workspace arms — collapsing the one blog whose job is multi-cloud
  coverage into yet another AWS-only lab. The fan-out must survive into §6's
  facets and §12's multi-runtime lab class.

- **No CVE — the vulnerability story is architectural.** There is no CVE to
  anchor on. An Extractor keyed on CVE strings will leave `vulnerability_story`
  thin or empty. The correct reading is that the "vulnerability" is a design
  property: **a federated IDP trusting SMS/push MFA that bridges multiple
  clouds**, so one compromised identity yields AWS + Azure + GWS at once. Flag
  for the Extractor: populate `vulnerability_story` from architectural prose,
  not from a CVE identifier.

- **Reproducibility over-rating risk (the mixed-derivation trap).** A naive
  Extractor is likely to mark steps 1–2 (SIM swap / stolen creds) as `full`
  because the *narrative* reads like concrete, executed actions. They are not
  lab-safe to reproduce fully — SIM swapping needs real telco infrastructure
  and real stolen creds cannot be ethically obtained, so they are
  `partial_simulation`. Mis-tiering these to `full` would wrongly classify the
  whole lab as `full` instead of `mixed`. The any-heterogeneity rule only fires
  correctly if the front steps are tiered honestly.

- **Most facets and value types are NOT in the AWS-only seed registry.**
  `target:azure`, `target:entra_id`, `target:gcp`, `target:okta`,
  `runtime:azure`, `runtime:gcp`, `okta_session_token`, `entra_refresh_token`,
  `aws_secretsmanager_secret`, `github_pat`, `code_signing_certificate` are
  genuine **proposals** against the v1 bundled vocabulary. Per **ADR 0099 §6**,
  manifest Layer-1 registry-membership validation is **not wired** into the
  `plan` pipeline, so a `plan`-produced `lab.yaml` declaring these would **not
  be rejected at plan time** — the membership check is an owned deferral to the
  Phase-3 `generate` Validator. The walk records them so the eventual
  registry-evolution step and the deferred membership check have a ground-truth
  inventory to reconcile against.

- **Passive / blended-agency wording (an Extractor disambiguation hazard).**
  The source uses living-off-the-land framing where the attacker *is* the
  authenticated user — "viewing documents," "leverage CloudShell," "create IAM
  Users." Because the actor operates through legitimate identities, the prose
  rarely says "the attacker" explicitly; it reads like ordinary admin activity.
  An Extractor must not be lulled into treating these as benign user actions:
  the disambiguation prompt should flag that **legitimate-looking console/SaaS
  activity performed by a compromised identity is the attack**, and attribute
  agency to the actor even when the verb has no explicit subject.

- **Phase 1 prompt-engineering hints.** (1) Detect profile-shaped sources and
  synthesize a canonical chain. (2) Maintain a cross-cloud entity table — the
  shared IDP session is the `depends_on` ancestor of the AWS, Azure, and GWS
  arms; dropping it severs the multi-cloud structure. (3) Source
  `vulnerability_story` from architectural prose when no CVE is present.
  (4) Tier human-factor steps (SIM swap, stolen creds) conservatively as
  `partial_simulation`, never `full`. (5) For `incident_analysis` blogs, treat
  the detection corpus (here, the 27 P0 rules) as first-class output, not
  background color.
