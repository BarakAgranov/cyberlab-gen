# Blog walk: `entra-id-actor-token-cross-tenant-global-admin`

> **PROVISIONAL — agent-drafted, pending a human ground-truth pass.** This walk
> was drafted by an LLM agent reading the source blog and curated research
> material, not by an independent human walker. Per ADR 0102 §6 and
> `eval.md §7.2`, an LLM reading the same blog is **not** independent ground
> truth (same model class, same blind spots), so **no eval calibration value may
> be locked against this walk until a human ground-truth pass reviews it.** It is
> honest enough to build the curated set and drive the harness; it is not yet a
> calibration reference.

A manual ground-truth reading of Dirk-Jan Mollema's CVE-2025-55241 disclosure,
mapping the blog's narrative onto the AttackSpec structure
(`docs/schema.md §4.8`).

---

## 1. Header

- **id:** `entra-id-actor-token-cross-tenant-global-admin` (matches
  `eval/blog-sets/manifest.yaml`)
- **shape:** `incident_analysis` (least-bad fit from the closed enum; see §15 —
  this is really a cloud-provider-flaw / vulnerability disclosure and the
  shape enum has no slot for it)
- **URL:** https://dirkjanm.io/obtaining-global-admin-in-every-entra-id-tenant-with-actor-tokens/
- **Canonical URL:** same as above (no observed redirect)
- **Title:** "One Token to rule them all — obtaining Global Admin in every
  Entra ID tenant via Actor tokens"
- **Publisher:** Dirk-Jan Mollema (Outsider Security)
- **Publisher kind:** `researcher_personal`
- **Authors:** Dirk-Jan Mollema
- **Publication date:** 2025-09-17
- **Accessed date:** 2026-06-19
- **Content hash:** TBD (the Extractor produces this mechanically; not computed
  for this walk)

## 2. One-paragraph summary

Independent researcher Dirk-Jan Mollema disclosed CVE-2025-55241 (CVSS 10.0), a
cross-tenant privilege-escalation flaw in Microsoft Entra ID. He found that
undocumented Microsoft-internal "Actor tokens" — service-to-service delegation
tokens issued by the legacy Access Control Service, valid 24 hours, unrevocable,
and exempt from Conditional Access — could be requested inside an
attacker-controlled tenant and then embedded in an unsigned (`"alg":"none"`)
impersonation JWT naming a victim tenant ID and a target user's netId. The legacy
Azure AD Graph API (`graph.windows.net`) validated the Actor token's signature
and resource host but failed to verify that the impersonation token's tenant
matched the tenant the Actor token was issued in, so it served and mutated any
tenant's directory as any user — including a Global Admin — with no API-level
logging in the victim tenant. Microsoft was notified 2025-07-14, deployed a
global fix within three days (2025-07-17), added a further mitigation 2025-08-06,
published the CVE 2025-09-04, and the researcher published the technical
write-up 2025-09-17.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types:** `vulnerability_chain`, `cloud_provider_flaw`,
  `cross_tenant_compromise`, `privilege_escalation`, `incident_analysis`. The
  load-bearing types are the first four; `incident_analysis` is carried only to
  stay consistent with the chosen `shape` (a reviewer may prefer to drop it —
  see §15).
- **Summary:** A missing tenant-origin validation in the legacy Azure AD Graph
  API, combined with the design of undocumented Microsoft-internal Actor tokens
  and the unsigned impersonation-token construction that Microsoft services use
  internally, let an attacker who controls any single Entra tenant forge a
  cross-tenant impersonation token and act as any user (including Global Admin)
  in any other Entra tenant worldwide, with no API-level logging in the victim
  tenant.
- **Attacker objective:** Obtain Global Admin in an arbitrary victim Entra ID /
  Microsoft 365 tenant from a self-controlled tenant; read and modify the victim
  directory (users, groups, devices, BitLocker keys, app credentials); pivot
  into M365/Azure — undetectably.
- **Vulnerability story:** **Substantive.** Two compounding root causes.
  **(1) Actor tokens** — an undocumented S2S delegation token issued by the
  legacy Access Control Service: a signed JWT, valid 24 hours, unrevocable in
  that window, carrying `trustedfordelegation`, exempt from Conditional Access,
  whose issuance generates no logs relevant to the victim (any logs land in the
  requester's own tenant). A Microsoft first-party app (e.g. Exchange Online)
  requests one for a target service such as Azure AD Graph.
  **(2) The legacy Azure AD Graph API (`graph.windows.net`)** accepts an
  **unsigned** outer JWT (`"alg":"none"`) that embeds the signed Actor token in
  an `actortoken` claim and names an arbitrary `aud` (resource host + victim
  tenant ID) and `nameid` (the impersonated user's netId). It validated the
  Actor token signature and the resource host but did **not** validate that the
  impersonation token's tenant matched the tenant the Actor token was issued in
  — so a token forged in tenant A was accepted against tenant B. Because netIds
  (the `puid`) are sequential rather than random, a valid target user could be
  found by brute force in minutes-to-hours (no victim-tenant logs), read from a
  guest user's `alternativeSecurityIds` attribute, or harvested from
  leaked/expired tokens. The successor Microsoft Graph performs the tenant check
  and has API-level logging, so it was unaffected. Microsoft fixed the
  validation globally within three days and later blocked Actor-token issuance
  for Azure AD Graph with service-principal credentials.

## 4. Chain steps

Seven steps. Step shape mirrors `schema.md §4.8` lines 358–383. The blog states
no MITRE technique IDs; all IDs below are walker-assigned by mapping the
described behavior (see §15 — an Extractor must not claim these are from the
blog).

### Chain step 1: Establish an attacker-controlled tenant and request an Actor token

- **Blog excerpt:**
  > Actor tokens are tokens that are issued by the "Access Control Service".
- **Description:** From a tenant the attacker fully controls (a free/trial Entra
  tenant), cause a Microsoft first-party application (e.g. Exchange Online) to
  request an Actor token from the legacy Access Control Service for a target
  resource (Azure AD Graph). The result is a legitimately signed JWT, valid 24
  hours, unrevocable, carrying `trustedfordelegation`; its issuance produces no
  logs relevant to the victim.
- **MITRE techniques:** `T1078.004`, `T1606.002`
- **Preconditions:** An attacker-owned Entra tenant; a first-party app that can
  request Actor tokens for Azure AD Graph.
- **Postconditions:** Attacker holds a valid signed Actor token for Azure AD
  Graph.
- **Outputs:** `entra_actor_token`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** Actor-token issuance is a
  Microsoft-internal Access Control Service capability, never customer-facing,
  and is now blocked for Azure AD Graph with SP credentials (2025-08-06). A lab
  cannot mint a real signed Actor token against Microsoft's production backend;
  it can only be shown/explained or replayed from a captured artifact. Not full
  or partial because no part of the real signing path can be exercised in a
  self-contained lab.

### Chain step 2: Resolve the victim tenant ID from a public domain

- **Blog excerpt:**
  > Find the tenant ID for the victim tenant, this can be done using public APIs based on the domain name.
- **Description:** Look up the victim organization's tenant GUID from its public
  domain using unauthenticated, public Microsoft endpoints (e.g. the OpenID
  configuration / tenant-resolution endpoints). Requires no victim access and
  leaves no victim-side trace.
- **MITRE techniques:** `T1590.002`, `T1596`
- **Preconditions:** Knowledge of a victim domain name.
- **Postconditions:** Attacker holds the victim's tenant GUID.
- **Outputs:** `entra_tenant_id`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Tenant-ID resolution uses genuinely
  public, still-live, unauthenticated endpoints; a lab can perform it for real
  against any chosen domain (or a lab-owned tenant) verbatim, independent of the
  patched flaw. This is the one fully-runnable step.

### Chain step 3: Discover a valid target user netId in the victim tenant

- **Blog excerpt:**
  > Unlike object IDs, which are randomly generated, netIds are actually incremental.
- **Description:** Obtain a valid netId (the `puid`) for at least one victim user
  via any of three methods: (a) brute force, exploiting that netIds are
  sequential and that probing them through the flaw produced no victim-tenant
  logs; (b) reading the netId stored on the `alternativeSecurityIds` attribute of
  a guest account; or (c) harvesting netIds (the `puid` claim) from leaked,
  screenshotted, logged, or expired tokens.
- **MITRE techniques:** `T1589.002`, `T1087.004`, `T1110`
- **Preconditions:** Victim tenant ID (step 2); for (a), reachability of the
  vulnerable graph path.
- **Postconditions:** Attacker holds a valid target-user netId.
- **Outputs:** `entra_user_netid`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** All three methods are entangled with the
  flaw or with Microsoft-side production data. Brute force (a) was only
  feasible-and-silent because the vulnerable `graph.windows.net` path returned
  distinguishable responses without victim logging — that path is patched.
  Methods (b)/(c) depend on real cross-tenant guest metadata or real leaked
  tokens. Illustrable, not safely runnable end-to-end; not full/partial because
  the load-bearing oracle (the patched API) is gone.

### Chain step 4: Forge an unsigned cross-tenant impersonation token

- **Blog excerpt:**
  > When using this Actor token, Exchange would embed this in an unsigned JWT that is then sent to the resource provider.
- **Description:** Construct an outer JWT with header `"alg":"none"` whose body
  carries the signed Actor token in an `actortoken` claim, sets `aud` to the
  resource host plus the **victim** tenant ID
  (`00000002-0000-0000-c000-000000000000/graph.windows.net@<victim-tenant>`),
  sets `nameid` to the target user's netId, and sets
  `nii":"urn:federation:MicrosoftOnline`. This is the same impersonation
  construction Microsoft services use internally, repurposed cross-tenant.
- **MITRE techniques:** `T1606.002`, `T1134.001`
- **Preconditions:** A real Actor token (step 1); victim tenant ID (step 2);
  target netId (step 3).
- **Postconditions:** Attacker holds a forged cross-tenant impersonation token.
- **Outputs:** `entra_impersonation_token`
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** The forging itself is pure local JWT
  construction (set `alg:none`, paste `actortoken`, set `aud`/`nameid`) and can
  be scripted/shown verbatim in a lab. But it is only meaningful with a real
  Actor token from step 1 (`demonstration_only`) as input, so end-to-end it is at
  best partly stubbed; the construction can be demonstrated against a placeholder
  Actor token. Hence `partial_simulation`, not `full`. (See §15 — an LLM is
  likely to over-rate this as `full` by looking only at the local crafting.)

### Chain step 5: Authenticate to the legacy Azure AD Graph API and bypass the tenant check

- **Blog excerpt:**
  > Somehow the API seemed to accept my token even with the mismatching tenant.
- **Description:** Send the forged impersonation token to `graph.windows.net`
  targeting the victim tenant. The API validates the embedded Actor token's
  signature and the resource host but does **not** validate that the
  impersonation token's tenant matches the tenant the Actor token was issued in,
  so a token minted in the attacker's tenant is accepted against the victim
  tenant. Conditional Access and MFA are bypassed (Actor tokens are exempt).
- **MITRE techniques:** `T1550.001`, `T1078.004`
- **Preconditions:** Forged impersonation token (step 4).
- **Postconditions:** Authenticated Azure AD Graph session against the victim
  tenant as the impersonated user.
- **Outputs:** `entra_graph_api_session`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** This is the core provider-side
  vulnerability and it is patched (global tenant-origin validation 2025-07-17;
  Actor-token issuance for Azure AD Graph blocked 2025-08-06). The cross-tenant
  acceptance cannot be reproduced against real Microsoft infrastructure, and
  there is no customer-controllable `graph.windows.net` to stand up. Shown only.

### Chain step 6: Enumerate the victim directory and identify a Global Admin

- **Blog excerpt:**
  > Since there is no API level logging, it means the following Entra ID data could be accessed without any traces.
- **Description:** Acting as the impersonated user against `graph.windows.net`,
  read the victim directory — users, groups, roles, devices, BitLocker recovery
  keys, application/service-principal credentials — and identify a Global
  Administrator account and its netId. Because the legacy API has no API-level
  logging, none of this generates traces in the victim tenant.
- **MITRE techniques:** `T1087.004`, `T1069.003`, `T1526`, `T1213`
- **Preconditions:** Authenticated Graph session (step 5).
- **Postconditions:** Attacker holds the victim directory contents and a Global
  Admin netId.
- **Outputs:** `entra_directory_data`, `entra_user_netid`,
  `bitlocker_recovery_key`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** Read access is achieved only through the
  patched cross-tenant acceptance of step 5; with the flaw closed there is no way
  to run these reads against a real victim tenant, and the no-logging property is
  a property of the now-superseded legacy API. Narratable, not safely runnable.

### Chain step 7: Forge a Global Admin impersonation token and take over the tenant

- **Blog excerpt:**
  > They completely bypass any restrictions configured in Conditional Access.
- **Description:** Repeat the forge-and-call cycle with the Global Admin's netId,
  obtaining read/write Global Admin authority over the victim tenant via Azure AD
  Graph: create users, assign the Global Admin role, modify Conditional Access
  policies, manipulate application credentials — then pivot into Microsoft 365
  (Exchange, SharePoint) and Azure subscriptions. Write operations produce
  confused audit logs (impersonated user's UPN + the impersonated Microsoft
  service's display name) — the only detectable residue.
- **MITRE techniques:** `T1098.003`, `T1136.003`, `T1484.002`, `T1550.001`
- **Preconditions:** Global Admin netId (step 6); ability to forge/replay
  (steps 4–5).
- **Postconditions:** Full administrative control of the victim tenant and
  pivot capability into M365/Azure.
- **Outputs:** `entra_global_admin_access`, `entra_directory_data`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** Full tenant takeover is a downstream
  consequence of the patched cross-tenant flaw (steps 5–6), inherently
  victim-specific and destructive (creating Global Admins, rewriting Conditional
  Access in a real tenant). It cannot be staged against real Microsoft
  infrastructure post-fix and must not be run against a victim; demonstration
  only.

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched. The three
netId-discovery methods in step 3 are alternative *means to a single
precondition* within one step, not alternative attack paths; the impersonation
mechanism (steps 4–7) is single and unbranched.

## 6. Facets

Per `schema.md §4.13`. Walker's best estimate; categories tagged with who is
*architecturally* responsible (Extractor for blog-derived; Planner for
lab-derived).

### 6.1 `target:*` (Extractor-derived)

`target:azure`, `target:entra_id`, `target:azure_ad_graph`.

### 6.2 `runtime:*` (Planner-derived; walker's guess)

`runtime:azure` (first-class) and `runtime:local` (first-class — where the
JWT-forging sandbox and KQL-detection demo live). **Deep caveat (see §15):** the
*real* runtime the exploit needs — Microsoft's internal Access Control Service
plus the legacy `graph.windows.net` backend — is neither first-class nor
provisionable at all; it is provider-internal. That un-provisionable runtime,
not a non-first-class one, is why most steps are `demonstration_only`.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

`lab_class_signal:vulnerability_chain`, `lab_class_signal:cross_tenant`,
`lab_class_signal:incident_analysis`, `lab_class_signal:provider_internal`,
`lab_class_signal:no_logging`, `lab_class_signal:expected_detections`.

## 7. Value types referenced

All **proposed/new** — the bundled v1 registry is AWS-only seed, so every type
below is flagged `(proposed)`:

- `entra_actor_token` (proposed) — the signed, Microsoft-issued legitimate
  delegation primitive. Distinct from the impersonation token (see §15).
- `entra_impersonation_token` (proposed) — the unsigned `alg:none`
  attacker-crafted JWT that embeds the Actor token. Conflating it with
  `entra_actor_token` loses the entire mechanism.
- `entra_tenant_id` (proposed) — victim tenant GUID resolved from a domain.
- `entra_user_netid` (proposed) — the `puid`, **sequential** (not the random
  object/GUID id); the sequentiality is the load-bearing property that makes
  brute force feasible. Typing it as a generic `user_id` loses that.
- `entra_graph_api_session` (proposed) — the authenticated Azure AD Graph
  session against the victim tenant.
- `entra_directory_data` (proposed) — the readable directory contents (users,
  groups, roles, devices, app creds).
- `entra_global_admin_access` (proposed) — an authority/capability value type,
  not a credential.
- `bitlocker_recovery_key` (proposed) — a concrete high-impact output surfaced
  as readable directory data; worth typing separately.

## 8. Defender techniques

This is a vulnerability disclosure, not an incident reconstruction — but the
blog contains a substantive, pedagogically load-bearing **detection
methodology**, so the section is populated (judgment call; flag for reviewer —
see §15). Per `schema.md §4.8` lines 403–414.

- **Name:** Hunt for Microsoft first-party service display names on write
  operations
  - **Technique kind:** `threat_hunting`
  - **Applies to chain steps:** step 7 (writes are the only logged residue)
  - **Description:** KQL over `AuditLogs` for `InitiatedBy.user.displayName`
    matching Microsoft first-party service display names (Office 365 Exchange
    Online, Skype for Business Online, Dataverse, Office 365 SharePoint Online,
    Microsoft Dynamics ERP) on non-group write operations — the only residue of
    Actor-token abuse.
- **Name:** Treat impersonated-UPN + service-display-name pairing as a signature
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 7
  - **Description:** Treat the anomalous pairing of an impersonated-user UPN with
    a Microsoft service display name in Entra audit logs as an Actor-token-abuse
    signature.
- **Name:** Migrate off legacy Azure AD Graph to Microsoft Graph
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** steps 5–6 (no-logging reads)
  - **Description:** Microsoft Graph performs tenant-origin validation and emits
    API-level logging, removing the unlogged-read blind spot.

## 9. Defenses

Controls per `schema.md §4.8` lines 416–426. These are vendor remediations, not
customer tradecraft (see §15 — an LLM may misread the three Microsoft actions as
a defender TTP chain).

- **Description:** Microsoft deployed global tenant-origin validation on the
  Azure AD Graph token path (2025-07-17), rejecting impersonation tokens whose
  tenant differs from the Actor token's originating tenant.
  - **Applicability:** `vendor_only`
  - **Addresses chain steps:** step 5 (and everything downstream)
- **Description:** Microsoft blocked applications from requesting Actor tokens
  for the Azure AD Graph API with service-principal credentials (2025-08-06).
  - **Applicability:** `vendor_only`
  - **Addresses chain steps:** step 1
- **Description:** Retire the legacy Azure AD Graph API (`graph.windows.net`) in
  favor of Microsoft Graph, which validates tenant origin and provides API-level
  logging.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** steps 5–6
- **Description:** Defender hunt: KQL over `AuditLogs` flagging write operations
  whose `InitiatedBy` displayName is a Microsoft first-party service paired with
  an unexpected user UPN.
  - **Applicability:** `detection_only`
  - **Addresses chain steps:** step 7
  - **Detection path / format:** KQL query over Entra `AuditLogs`.

## 10. External references

- **CVEs cited:** `CVE-2025-55241` (CVSS 10.0 — sourced from MSRC/secondary
  advisories, **not** stated in the blog body; see §15).
- **Related blogs:**
  https://msrc.microsoft.com/update-guide/vulnerability/CVE-2025-55241 ,
  https://practical365.com/death-by-token-understanding-cve-2025-55241/
- **MITRE ATT&CK techniques referenced:** none — the blog cites no technique
  IDs. Every ID in §4 is walker-inferred, not blog-asserted.

## 11. Real-world incidents

Per `schema.md §4.8` lines 339–356.

- **Status:** `none_observed`
- **Evidence source:** Microsoft stated it detected no abuse of the
  vulnerability in its telemetry prior to remediation; the capability was
  demonstrated only by the disclosing researcher, who halted testing at
  Microsoft's request (2025-07-15).
- **Incidents:** none (status ≠ `incidents_documented`).

## 12. Expected lab class

- **Lab kind:** "Cross-tenant Entra ID vulnerability-disclosure lab" — a
  demonstration-grade Azure/Entra ID lab teaching the Actor-token forgery and
  the missing-tenant-check root cause, plus a detection (KQL) component.
- **Why this class:** `target:azure` + `target:entra_id` +
  `target:azure_ad_graph` with `lab_class_signal:vulnerability_chain`,
  `:cross_tenant`, `:provider_internal`, and `:no_logging` point at a
  cross-tenant Entra disclosure whose exploit primitives are Microsoft-internal
  and patched. The Planner should provision a `runtime:azure` plus a
  `runtime:local` token-forging/JWT sandbox and a detection component, rather
  than attempt a live cross-tenant exploit — because the load-bearing runtime
  (`graph.windows.net` + Access Control Service) is un-provisionable, the lab is
  largely `demonstration_only` (see §13).

## 13. Reproducibility (lab-level)

Derived from the per-step tiers in §4, applying the any-heterogeneity-mixed rule
(`schema.md §4.8` line 438): if required steps span tiers, the lab is `mixed`.

- **Classification (lab-level):** `mixed`
- **Caveats:** Heavily skewed toward `demonstration_only`. The load-bearing
  primitives — minting a real signed Actor token (step 1), the cross-tenant
  acceptance by `graph.windows.net` (step 5), and everything downstream (steps
  3, 6, 7) — depend on Microsoft-internal infrastructure that is now patched and
  was never customer-controllable, so they cannot be safely or really run in a
  lab. Only two mechanics survive as runnable: tenant-ID resolution from a domain
  (step 2, `full`) and the local JWT construction (step 4, `partial_simulation`,
  since it needs a real Actor token to be meaningful end-to-end). A faithful lab
  teaches the token mechanics and the validation flaw conceptually plus the
  defender KQL hunt, but cannot execute the cross-tenant takeover.
- **Derivation trace:** Per-step tiers — step 1 = `demonstration_only`;
  step 2 = `full`; step 3 = `demonstration_only`; step 4 = `partial_simulation`;
  step 5 = `demonstration_only`; step 6 = `demonstration_only`;
  step 7 = `demonstration_only`. No step is `not_reproducible` (each is at least
  meaningfully demonstrable per the demonstration-must-be-meaningful floor,
  `schema.md §4.20`), so none is excluded as non-required. The required steps
  span three tiers `{full, partial_simulation, demonstration_only}` ≥ 2 tiers,
  therefore by the any-heterogeneity rule the lab is **`mixed`** — specifically
  steps 2 and 4 being runnable while the core (1, 3, 5, 6, 7) is not is exactly
  what makes it mixed rather than uniformly `demonstration_only`.
- **Overall assessment:** Mixed but effectively a demonstration-grade lab: the
  chain's two root causes (Actor-token issuance and the missing tenant-origin
  check on legacy Azure AD Graph) are provider-internal and patched, so the
  exploit is not runnable for real; the honest learner framing is "understand the
  token forgery and the validation gap, run the public tenant-resolution and the
  local token-crafting, and detect the abuse via audit-log signatures" rather
  than "execute a cross-tenant takeover."

## 14. Coverage tags

Drives `coverage_tags:` in `eval/blog-sets/manifest.yaml`. Required by the brief:
`cloud:azure`, `vulnerability_disclosure`, `thesis:cloud_provider_flaw`,
`mixed_reproducibility`.

- `cloud:azure`
- `target:entra_id`
- `complexity:medium` (7 chain steps)
- `thesis:vulnerability_chain`
- `thesis:cross_tenant_compromise`
- `thesis:cloud_provider_flaw`
- `thesis:privilege_escalation`
- `vulnerability_disclosure` (the `vulnerability_story` is substantive)
- `incident_analysis` (defender techniques present)
- `mixed_reproducibility`
- `cve:CVE-2025-55241`
- `cvss:10.0`
- `provider_internal`
- `no_logging`
- `non_first_class_runtime` (the real exploit runtime is un-provisionable;
  see §6.2 / §15)

## 15. Manual ground-truth notes

**The highest-value section.** What a human walker would notice that an LLM
Extractor on the same blog might miss.

- **Source verification:** The chosen URL (dirkjanm.io) loaded fully; no
  alternative was needed. **CVSS 10.0 and CVE-2025-55241 were confirmed via
  external advisories (MSRC / secondary), because the blog body itself does
  not state the CVSS score** — a verbatim search for the score on the page
  returns NOT FOUND. This is itself a high-value Extractor trap: an LLM reading
  only the blog would be correct to leave CVSS unstated and **wrong** to
  hallucinate it. The score here is sourced externally and tagged as such,
  keeping per-blog ground truth honest about blog-asserted vs. externally-true.

- **Shape call (enum gap):** `shape` was set to `incident_analysis` as the
  least-bad fit from the closed enum `{aws_ttp, supply_chain,
  incident_analysis}`, but this is genuinely a **vulnerability-disclosure /
  cloud_provider_flaw** blog, **not** a real-incident reconstruction
  (`none_observed`). `aws_ttp` is wrong (it's Azure, and a provider bug not
  customer tradecraft) and `supply_chain` is wrong. **Candidate new manifest
  shape:** `azure_vuln_disclosure` / `cloud_provider_flaw` (mirrors the same gap
  flagged in the codebuild walk). The real signal lives in `thesis.types`
  (`vulnerability_chain`, `cloud_provider_flaw`, `cross_tenant_compromise`,
  `privilege_escalation`); `incident_analysis` is in `thesis.types` only for
  consistency with the chosen shape and a reviewer may prefer to drop it.

- **Defender techniques on a disclosure (judgment call):** §8 is populated even
  though the field is nominally "for `incident_analysis` blogs only," because the
  blog's detection content (the KQL hunt) is real and pedagogically load-bearing.
  Surfaced rather than discarded; flag for reviewer.

- **Runtime / first-class (the deep caveat):** `runtime:azure` and
  `runtime:local` are both first-class. I did **not** add a non-first-class
  runtime — but the *real* runtime the exploit needs (Microsoft's internal
  Access Control Service + the legacy `graph.windows.net` backend) is neither
  first-class nor provisionable at all; it is provider-internal. That is why most
  steps are `demonstration_only`. The `non_first_class_runtime` tag and the
  `provider_internal` lab_class_signal make this explicit; the honest reading is
  that the most important runtime is un-provisionable, not merely non-first-class.
  A naive Planner might emit `runtime:azure` and assume the chain runs — it does
  not.

- **Reproducibility judgment (the hard part):** The instinct to call the whole
  thing `not_reproducible` was resisted via the demonstration-must-be-meaningful
  floor (`schema.md §4.20`): each provider-internal step can be meaningfully
  illustrated (show the token JSON, explain the missing tenant check, replay a
  captured token's structure), so `demonstration_only` is correct over
  `not_reproducible`. Step 2 (tenant resolution) is genuinely `full` — public
  endpoints, still live, unrelated to the flaw; an Extractor that lumps the whole
  chain into one tier would miss this. Step 4 (token forging) is the subtle one:
  locally it is full-fidelity JWT construction, but **useless without a real
  Actor token (step 1, `demonstration_only`)** as input, so end-to-end it is
  `partial_simulation`, not `full`. An LLM is likely to over-rate step 4 as
  `full` by looking only at the local crafting and ignoring its un-mintable
  dependency. The lab's mixedness comes precisely from steps 2 and 4 being
  runnable while the core (1, 3, 5, 6, 7) is not.

- **Value-type distinctions an Extractor will likely blur:** (1)
  `entra_actor_token` (signed, Microsoft-issued, legitimate primitive) vs.
  `entra_impersonation_token` (unsigned `alg:none`, attacker-crafted, embeds the
  Actor token) — two different tokens; conflating them loses the whole mechanism.
  (2) `entra_user_netid` is the `puid`, **sequential**, NOT the random
  object/GUID id — the sequentiality is what makes brute force feasible; typing
  it as a generic `user_id` loses the load-bearing property. (3)
  `bitlocker_recovery_key` as readable directory data is a concrete high-impact
  output worth typing separately. (4) `entra_global_admin_access` is an
  authority/capability value type, not a credential.

- **MITRE inference:** The blog states no MITRE techniques (consistent with
  dirkjanm's style). All IDs are walker-assigned by mapping behavior:
  `T1606.002` (Forge Web Credentials) is the spine of steps 1 and 4;
  `T1550.001` (Application Access Token) for replaying the forged token in
  steps 5/7; `T1078.004` (Valid Accounts: Cloud) for cross-tenant impersonation;
  `T1484.002` (Tenant Policy Modification) and `T1098.003` (Additional Cloud
  Roles) for the takeover. **An Extractor must not claim these are from the
  blog** — they are inferred. Over-tagging was avoided.

- **LLM Extractor failure modes to watch:** (a) Hallucinating CVSS/CVE specifics
  not in the blog body — must distinguish blog-asserted from externally-true.
  (b) Collapsing the two token types into one. (c) Over-rating reproducibility:
  this looks like a "cloud" (Azure) lab so an LLM may default it to `full`/
  `partial`, missing that the exploit primitives are provider-internal and
  patched — the correct dominant tier is `demonstration_only`. (d) Treating
  Microsoft's three remediation actions (2025-07-17 fix, 2025-08-06 block,
  Microsoft Graph being safe) as a defender TTP chain rather than vendor
  defenses. (e) Mis-reading the "no logging" claim — it is specifically NO
  API-level logging on the **legacy** Azure AD Graph for **reads**, while writes
  produce confused logs and Microsoft Graph does log; an LLM may overgeneralize
  to "no logging at all." (f) Treating netId brute force as "runnable" — it was
  feasible only because of the unlogged, now-patched oracle, so it is
  `demonstration_only`, not `full`. (g) Confusing the invite direction in the
  `alternativeSecurityIds` method — a guest invited into tenant A has *their*
  home-tenant netId stored in A, so a guest in YOUR tenant leaks THEIR home
  tenant's netId; the blog stresses this "against the invite direction" point and
  it is easy to get backwards.

- **Timeline (no schema slot; preserved here):** 2025-07-14 reported to MSRC;
  2025-07-15 MSRC asked to halt testing; 2025-07-17 global fix in production
  (3 days); 2025-07-23 MSRC confirmed resolution; 2025-08-06 further mitigation
  blocking Actor-token issuance for Azure AD Graph with SP creds; 2025-09-04
  CVE-2025-55241 published; 2025-09-17 technical disclosure blog. Belongs in a
  Manifest extras block.

- **Phase 1 prompt-engineering hints:** the Extractor prompt should (1)
  explicitly separate the signed Actor token from the unsigned impersonation
  token as distinct entities; (2) require provenance tagging that distinguishes
  blog-asserted facts from externally-sourced ones (the CVSS trap); (3) instruct
  that a step's reproducibility tier must account for its *inputs'* tiers (the
  step-4 over-rating trap); and (4) warn against reading vendor remediations as
  defender tradecraft.
