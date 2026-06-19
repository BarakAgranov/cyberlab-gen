# Blog walk: `gke-fluentbit-anthos-dual-privesc`

> **PROVISIONAL — AGENT-DRAFTED.** This walk was drafted by an agent from the
> structured source material, not read end-to-end by a human. It ships
> **provisional, pending a human ground-truth pass**. Per ADR 0102 and
> `eval.md §7.2`, **no eval calibration value may be locked against an
> unreviewed walk** — the per-step and lab-level reproducibility tiers, facets,
> and value-type mappings below must be confirmed by a human walker before this
> entry is used as gold-standard reference for Extractor/Planner scoring.

A manual ground-truth reading of a curated blog. The walker reads the blog
end-to-end and maps its narrative onto the AttackSpec structure
(`docs/schema.md §4.8`). This blog is the curated set's dedicated **`mixed`
reproducibility** example, and it adds `cloud:gcp` + `platform:kubernetes`
coverage.

---

## 1. Header

- **id:** `gke-fluentbit-anthos-dual-privesc` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `vulnerability_disclosure` (resolved per ADR 0103 — shape is a
  descriptive open-set label, not a closed enum; the blog is a GCP/Kubernetes
  privilege-escalation vulnerability disclosure. Matches
  `eval/blog-sets/manifest.yaml`. See §15.)
- **URL:** https://unit42.paloaltonetworks.com/google-kubernetes-engine-privilege-escalation-fluentbit-anthos/
- **Canonical URL:** https://unit42.paloaltonetworks.com/google-kubernetes-engine-privilege-escalation-fluentbit-anthos/
- **Title:** "Dual Privilege Escalation Chain: Exploiting Monitoring and Service
  Mesh Configurations and Privileges in GKE to Gain Unauthorized Access in
  Kubernetes"
- **Publisher:** Palo Alto Networks Unit 42
- **Publisher kind:** `vendor_lab`
- **Authors:** Shaul Ben Hai
- **Publication date:** 2023-12-27
- **Accessed date:** 2026-06-19
- **Content hash:** TBD (the Extractor produces this mechanically)

## 2. One-paragraph summary

Unit 42 chained two GKE provider-internal flaws into a full Kubernetes cluster
takeover. A second-stage attacker who already has code execution inside the
default FluentBit logging DaemonSet abuses its `hostPath` mount of
`/var/lib/kubelet/pods` to read the projected service-account tokens of every
other pod on the node — including the Anthos Service Mesh (ASM) Istio-cni-node
DaemonSet's token, which retains excessive RBAC after installation. With that
token the attacker creates a privileged pod in `kube-system` that mounts the
clusterrole-aggregation-controller (CRAC) service-account token, then abuses
CRAC's designed ability to add arbitrary permissions to existing cluster roles
to self-escalate to `cluster-admin`, gaining complete control of the cluster.
Neither flaw is individually critical; the chain is. Google fixed both issues on
2023-12-14 under bulletin GCP-2023-047 (removing the FluentBit pod-directory
mount and re-architecting the ASM CNI ClusterRole); no CVE was assigned.

## 3. Thesis

- **Types:** `ttp_chain`, `privilege_escalation`, `misconfiguration`,
  `cloud_provider_flaw`.
- **Summary:** Two independently low-impact GKE add-on misconfigurations —
  FluentBit's over-broad `hostPath` token exposure and the ASM CNI DaemonSet's
  retained excessive RBAC — chain through a generic Kubernetes CRAC
  self-escalation into full `cluster-admin` compromise.
- **Attacker objective:** Escalate from code execution inside a node-local
  logging container to `cluster-admin` control of the entire GKE Kubernetes
  cluster.
- **Vulnerability story:** Substantive (this is a chained misconfiguration /
  provider-flaw disclosure, not a single CVE). **Flaw 1:** GKE's bundled
  FluentBit DaemonSet mounts the host's `/var/lib/kubelet/pods` directory, so a
  compromised FluentBit pod can read the `kube-api-access` projected
  service-account tokens of every co-located pod, and because FluentBit runs on
  every node the attacker can sweep the whole cluster's tokens node by node.
  **Flaw 2:** when Anthos Service Mesh is enabled, the Istio-cni-node DaemonSet
  keeps installation/repair RBAC it no longer needs — powerful enough to create
  pods in `kube-system`. The exploitable consequence: an attacker who lifts the
  CNI token creates a pod that mounts the CRAC token; CRAC can add arbitrary
  permissions to existing cluster roles, so the attacker rewrites CRAC's bound
  ClusterRole to grant all privileges and becomes `cluster-admin`. Google
  remediated both under GCP-2023-047 (2023-12-14).

## 4. Chain steps

Six ordered steps. The chain is narrated by the source as "two issues"
(FluentBit, ASM) plus a CRAC finale, with a stipulated initial-access
precondition; it is split fine here to expose where the heterogeneity lives
(see §15 step-boundary judgment).

### Chain step 1: Initial access to the FluentBit container (second-stage prerequisite)

- **Blog excerpt:**
  > Since this is a second-stage attack, the attacker must first exploit the FluentBit container by discovering a remote code execution or arbitrary file read vulnerability, or otherwise breaking out of another container to gain access to the Node.
- **Description:** The chain assumes the attacker already has code execution
  inside the FluentBit logging container (the default GKE logging DaemonSet) or
  has otherwise broken out of another container onto the node. The article
  supplies no concrete exploit; it explicitly frames this as a precondition
  (RCE, arbitrary file read, or container escape).
- **MITRE techniques:** `T1190`, `T1611`
- **Preconditions:** A reachable, vulnerable workload on the node, or a
  container-escape primitive (none named in the source).
- **Postconditions:** Code execution inside the FluentBit pod / on the node.
- **Outputs (value-type names):** `container_foothold`
- **Reproducibility:** `not_reproducible`
- **Why this tier (not a higher one):** The blog provides no concrete exploit or
  vulnerable target — it is stipulated as a generic precondition. With no named
  CVE, payload, or vulnerable image, a lab cannot stage it as written. It is a
  **non-required** precondition: the lab grants the FluentBit foothold directly
  (e.g. an interactive shell in a FluentBit-like pod) rather than reproducing an
  arbitrary-RCE acquisition, so it is **excluded** from the lab-level
  any-heterogeneity rollup (§13).

### Chain step 2: Steal projected service-account tokens via FluentBit's `/var/lib/kubelet/pods` hostPath mount

- **Blog excerpt:**
  > The kube-api-access volume contains the projected service account token for a pod to communicate with the Kubernetes API, which is a sensitive piece of information.
- **Description:** From inside the FluentBit pod the attacker reads the host path
  `/var/lib/kubelet/pods` (mounted into FluentBit via `hostPath`). Each pod's
  directory contains a `kube-api-access-<suffix>` volume holding that pod's
  projected service-account token. The attacker harvests those tokens; because
  FluentBit is a DaemonSet running on every node, the sweep repeats node-by-node
  to collect tokens cluster-wide.
- **MITRE techniques:** `T1552.001`, `T1528`, `T1613`
- **Preconditions:** Step 1 foothold inside FluentBit; the GKE-bundled FluentBit
  DaemonSet shipping with the over-broad `/var/lib/kubelet/pods` mount.
- **Postconditions:** Possession of one or more other pods' projected
  service-account tokens.
- **Outputs (value-type names):** `kubernetes_service_account_token`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** The exposure depends on GKE's bundled
  FluentBit DaemonSet shipping with the over-broad `/var/lib/kubelet/pods`
  `hostPath` mount — a provider-internal default Google **removed on 2023-12-14
  (GCP-2023-047)**. On a current GKE cluster the mount is gone, so the
  token-theft cannot be reproduced as the blog describes without reverting
  Google's managed fix, which a lab cannot do. It can be *demonstrated* by
  hand-rolling a pod with that exact `hostPath` mount on a self-managed cluster,
  but that is a simulation of the misconfig, not the real provider-shipped
  vulnerable state — hence `demonstration_only`, not `full`.

### Chain step 3: Impersonate the stolen pod token and enumerate the cluster

- **Blog excerpt:**
  > Using the pod token, the attacker can impersonate a pod with privileged access to the Kubernetes API server and gain unauthorized access to the cluster.
- **Description:** The attacker authenticates to the Kubernetes API server with a
  harvested projected token (standard bearer-token auth) and enumerates the
  cluster — listing pods/namespaces and probing the token's permissions (e.g.
  `kubectl auth can-i` / `kubectl get`) to find a high-value identity. Generic
  Kubernetes tradecraft, independent of any GKE-specific flaw.
- **MITRE techniques:** `T1078.004`, `T1613`, `T1087`
- **Preconditions:** A valid service-account bearer token from Step 2; network
  reachability to the kube-apiserver.
- **Postconditions:** Authenticated API access and an enumerated view of the
  cluster's identities/permissions.
- **Outputs (value-type names):** `kubernetes_api_access`, `cluster_enumeration`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Authenticating to the kube-apiserver
  with a service-account bearer token and enumerating with `kubectl` is
  standard, unpatched, vendor-agnostic Kubernetes behavior that works on any
  cluster today. A lab can fully script it: provision a cluster, mint a token
  for a service account, and run the enumeration for real. Nothing about this
  step relies on the patched GKE-internal flaws. (Caveat: in the *real* chain
  the token comes from Step 2's patched flaw; a lab achieves `full` here only by
  granting the token directly — see §13.)

### Chain step 4: Identify and steal the Istio-cni-node (ASM CNI) token with retained excessive RBAC

- **Blog excerpt:**
  > The ASM's CNI DaemonSet retains excessive permissions post-installation. This allows an attacker to create a new pod with ASM's CNI DaemonSet permissions.
- **Description:** Among the swept tokens the attacker selects the Anthos Service
  Mesh Istio-cni-node DaemonSet's service-account token. That DaemonSet keeps
  the installation/repair RBAC it needed at setup but no longer requires —
  permissions powerful enough to create pods in `kube-system`. The attacker now
  holds a token that can create privileged workloads.
- **MITRE techniques:** `T1552.001`, `T1078.004`, `T1613`
- **Preconditions:** ASM enabled on the cluster (explicit prerequisite); Step 2
  token sweep reaching the node hosting the Istio-cni-node pod.
- **Postconditions:** Possession of the ASM CNI service-account token with
  pod-create rights in `kube-system`.
- **Outputs (value-type names):** `kubernetes_service_account_token`,
  `kubernetes_api_access`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** The exploitable surplus permission is a
  provider-internal property of the GKE-managed Anthos Service Mesh add-on,
  which Google **re-architected on 2023-12-14 (GCP-2023-047)** to strip the
  excessive RBAC. The real vulnerable state no longer exists on a managed
  GKE+ASM cluster, and a lab cannot revert Google's managed remediation. The
  over-permissioned RBAC could be *simulated* by manually creating an equivalent
  ClusterRole, but that is staging the misconfig, not reproducing the shipped
  flaw; and acquiring the token itself depends on Step 2's patched FluentBit
  exposure.

### Chain step 5: Create a privileged pod in `kube-system` that mounts the CRAC service-account token

- **Blog excerpt:**
  > The attacker will grant the CRAC's service account in the pod's YAML file and they will finally save the token in one of their own volume folders.
- **Description:** Using the Istio-cni-node token's pod-creation rights, the
  attacker applies a pod manifest in the `kube-system` namespace that sets
  `serviceAccountName` to the clusterrole-aggregation-controller (CRAC) service
  account, so the CRAC token is auto-mounted into the attacker's pod (which the
  blog notes they then copy into their own volume). `kube-system` was chosen
  because it holds preinstalled, extremely powerful service accounts.
- **MITRE techniques:** `T1610`, `T1098.006`, `T1613`
- **Preconditions:** The ASM CNI token (pod-create in `kube-system`) from Step 4.
- **Postconditions:** A running attacker-controlled pod in `kube-system` with the
  CRAC token mounted/exfiltrated.
- **Outputs (value-type names):** `kubernetes_pod`,
  `kubernetes_service_account_token`
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** The actual creation of a pod is generic
  Kubernetes that a lab can run — but **only** because of the over-permissioned
  CNI identity from Step 4, which is the patched provider flaw. As the blog
  presents it (creating the pod with the *real* ASM CNI DaemonSet's authority on
  managed GKE), it is not reproducible post-patch. The manifest is shown only as
  an **image (Figure 6)**, not verbatim text, so the exact spec must be
  reconstructed. Marked `demonstration_only` because the privilege that
  authorizes the creation is the patched flaw; a lab can simulate it by granting
  an equivalent pod-create role. (A defensible alternative reading is
  `full` — see §15.)

### Chain step 6: Self-escalate via CRAC to `cluster-admin`

- **Blog excerpt:**
  > The clusterrole-aggregation-controller (CRAC) service account is probably the leading candidate, as it can add arbitrary permissions to existing cluster roles.
- **Description:** Holding the CRAC token, the attacker abuses the
  clusterrole-aggregation-controller's designed ability to add arbitrary
  permissions to existing cluster roles: they update the ClusterRole bound to
  CRAC (via aggregation labels) to possess all privileges, effectively granting
  CRAC — and therefore themselves — `cluster-admin` and complete control of the
  cluster.
- **MITRE techniques:** `T1098.006`, `T1078.004`
- **Preconditions:** The CRAC service-account token from Step 5; CRAC's
  unchanged, by-design aggregation behavior.
- **Postconditions:** `cluster-admin` privileges; full cluster control.
- **Outputs (value-type names):** `kubernetes_cluster_admin`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** The CRAC self-escalation is a
  well-known, vendor-agnostic Kubernetes technique that is **not** part of the
  GCP-2023-047 fix (Google patched the two access flaws, not the
  existence/behavior of CRAC). Given the CRAC token, rewriting its aggregated
  ClusterRole to all-privileges works on any current cluster, so a lab can fully
  script and run it for real — once it grants the CRAC token (which the lab does
  directly, simulating the upstream theft).

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched. The
article presents one linear escalation path (FluentBit token sweep → ASM CNI
token → CRAC pod → `cluster-admin`); the "two issues" framing describes two
flaws within that single chain, not two alternative attack routes.

## 6. Facets

Walker's best estimate; categories tagged with who is *architecturally*
responsible (Extractor for blog-derived; Planner for lab-derived). Both are
recorded for ground-truth comparison.

### 6.1 `target:*` (Extractor-derived)

`target:gcp`, `target:kubernetes`, `target:gke`. The attack targets a managed
GKE cluster and its Kubernetes control plane; the GKE-bundled FluentBit
DaemonSet and the Anthos Service Mesh add-on are the specific attacked surfaces.

### 6.2 `runtime:*` (Planner-derived; walker's guess)

`runtime:gcp` (first-class — the faithful target is managed GKE),
`runtime:kubernetes` (**best-effort / non-first-class** — outside
`{aws, azure, gcp, github, local}`; flagged via the `non_first_class_runtime`
coverage tag), `runtime:local` (best-effort — a self-managed local Kubernetes,
e.g. kind/minikube, is the realistic substrate for *simulating* the patched
misconfigs, since a current managed GKE provision cannot reproduce Steps 2/4).
See §15 for why a pure-`runtime:gcp` planner choice would find the vulnerable
state unprovisionable.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

`lab_class_signal:ttp_chain`, `lab_class_signal:vulnerability_disclosure`,
`lab_class_signal:privilege_escalation`, `lab_class_signal:cloud_provider_flaw`,
`lab_class_signal:mixed_reproducibility`.

## 7. Value types referenced

- `container_foothold`
- `kubernetes_service_account_token`
- `kubernetes_api_access`
- `cluster_enumeration`
- `kubernetes_pod`
- `kubernetes_cluster_admin`

These are the value types the chain surfaces. They are Kubernetes-domain types;
if the v1 `registry/value_types.yaml` does not yet carry the Kubernetes set
(`kubernetes_service_account_token`, `kubernetes_api_access`,
`cluster_enumeration`, `kubernetes_pod`, `kubernetes_cluster_admin`,
`container_foothold`), they should be treated as **`(proposed)`** for
registry-evolution — justification: this is the curated set's first
Kubernetes/GKE blog, so the Kubernetes value-type vocabulary lands with it.
(A human reviewer should confirm which of these already exist before relying on
the membership status — consistent with the provisional banner.)

## 8. Defender techniques

Not applicable for this blog — no defender-investigation narrative is described
in the source (`real_world_incidents = none_observed`; this is an
attacker-perspective vulnerability disclosure, not an `incident_analysis`
write-up). Defensive content is captured as **defenses** in §9, not as defender
techniques.

## 9. Defenses

- **Description:** Google's GCP-2023-047 fix (2023-12-14) removed the
  `/var/lib/kubelet/pods` `hostPath` volume mount from the FluentBit pod,
  eliminating its access to other pods' projected service-account tokens.
  - **Applicability:** `vendor_only`
  - **Addresses chain steps:** Step 2.

- **Description:** Google's GCP-2023-047 fix modified the Anthos Service Mesh
  ClusterRole and re-architected ASM CNI functionality to remove the excessive
  post-installation RBAC.
  - **Applicability:** `vendor_only`
  - **Addresses chain steps:** Steps 4, 5.

- **Description:** Avoid over-broad `hostPath` mounts (especially
  `/var/lib/kubelet/pods`) on logging/monitoring DaemonSets; mount only the
  specific log directory needed.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** Step 2.

- **Description:** Apply least-privilege RBAC to add-on / system service
  accounts and drop installation-time permissions once setup completes.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** Steps 4, 5.

- **Description:** Restrict who/what can create pods in `kube-system` and bind
  sensitive service accounts (e.g. CRAC) so they cannot be mounted by arbitrary
  workloads.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** Steps 5, 6.

- **Description:** Monitor for anomalous Kubernetes actions — service-account
  credential theft, privileged pod creation in `kube-system`, and cluster-role
  modifications.
  - **Applicability:** `detection_only`
  - **Detection path / format:** Vendor tooling cited by the source — Prisma
    Cloud, Cortex XDR.
  - **Addresses chain steps:** Steps 2, 5, 6.

## 10. External references

- **CVEs cited:** None. GCP-2023-047 is a Google security bulletin, not a CVE;
  the article assigns no CVE. Do not hallucinate a `CVE-2023-xxxx` (see §15).
- **Related blogs / references:**
  - GCP-2023-047 (Google security bulletin)
  - https://cloud.google.com/kubernetes-engine/docs/security-bulletins
  - clusterrole-aggregation-controller (CRAC) privilege-escalation technique
  - Anthos Service Mesh / Istio CNI
  - FluentBit GKE logging DaemonSet
- **MITRE ATT&CK techniques referenced:** The blog does not cite ATT&CK IDs
  itself; the per-step techniques in §4 are analyst best-fit mapping (see §15),
  not source-stated.

## 11. Real-world incidents

- **Status:** `none_observed`
- **Evidence source:** This is a Unit 42 research disclosure of a
  responsibly-reported, vendor-patched flaw chain; the article describes no
  observed in-the-wild exploitation.
- **Incidents:** None (status is `none_observed`).

## 12. Expected lab class

- **Lab kind:** "GKE / Kubernetes privilege-escalation `ttp_chain` (dual
  misconfiguration → CRAC self-escalation to `cluster-admin`)."
- **Why this class:** The `target:gcp` + `target:kubernetes` + `target:gke`
  facets with `lab_class_signal:cloud_provider_flaw` and
  `lab_class_signal:privilege_escalation` point at a multi-phase Kubernetes lab.
  The `lab_class_signal:mixed_reproducibility` signal is the load-bearing one:
  the chain provably spans `full` (generic `kubectl`/CRAC steps) and
  `demonstration_only` (the patched FluentBit + ASM provider flaws) tiers, so
  the Extractor+Planner should land a lab-level **`mixed`** classification (§13).

## 13. Reproducibility (lab-level)

Derived from the per-step `reproducibility` values in §4, applying the
**any-heterogeneity-mixed rule** (`schema.md §4.8` line 438): if all *required*
steps share a tier, take that tier; if required steps span tiers, the lab is
`mixed`.

- **Classification (lab-level):** `mixed`
- **Caveats:** The two `full` steps (Step 3 enumeration, Step 6 CRAC
  self-escalation) are reproducible only with tokens that, in the *real* chain,
  come from the patched provider flaws; a lab reproduces them faithfully only by
  **granting those tokens directly** (simulating the upstream theft). So even
  the `full` steps run in a lab that has stubbed the `demonstration_only` links.
  The `not_reproducible` precondition (Step 1) is **excluded** as non-required.
  The two provider-internal flaws (Steps 2 and 4) are `demonstration_only`
  because Google's 2023-12-14 managed fix (GCP-2023-047) removed the vulnerable
  state and a lab cannot revert a managed-platform patch.
- **Derivation trace:** Apply the any-heterogeneity rule over the **required**
  set only.
  - Step 1 = `not_reproducible` → **excluded** as a non-required precondition
    (the lab grants the FluentBit foothold directly), so it does **not** by
    itself force a `not_reproducible` rollup.
  - Required steps and tiers: Step 2 = `demonstration_only`, Step 3 = `full`,
    Step 4 = `demonstration_only`, Step 5 = `demonstration_only`, Step 6 =
    `full`.
  - The required set spans **two tiers** — `full` (Steps 3, 6) **and**
    `demonstration_only` (Steps 2, 4, 5) — i.e. heterogeneous. Per the
    any-heterogeneity rule, **heterogeneous required tiers → `mixed`**. The
    presence of `full` scriptable privesc mechanics (CRAC self-escalation, Step
    6, when the lab grants the leaked token directly) **alongside** at least one
    `demonstration_only` in-chain prerequisite (the FluentBit token theft, Step
    2, and the ASM CNI RBAC theft, Step 4, both depending on the
    now-patched provider flaw) is exactly the heterogeneity the rule keys on.
  - Rollup = **`mixed`**.
- **Overall assessment:** Textbook mixed-reproducibility chain — generic,
  still-working Kubernetes tradecraft (token-based API enumeration in Step 3 and
  CRAC self-escalation in Step 6) that a lab can fully run, bookended by two GKE
  provider-internal add-on flaws (FluentBit token exposure in Step 2, ASM CNI
  excessive RBAC in Step 4) that were patched on 2023-12-14 and can only be
  demonstrated/simulated, not safely reproduced against managed GKE. A faithful
  lab scripts the `full` steps for real and stubs the patched flaws by directly
  granting the tokens/permissions they would have leaked.

## 14. Coverage tags

Drives the `coverage_tags:` field in `eval/blog-sets/manifest.yaml`:

- `cloud:gcp`
- `platform:kubernetes`
- `platform:gke`
- `non_first_class_runtime` (the lab targets best-effort `runtime:kubernetes`)
- `complexity:medium` (6 chain steps, in the 4–8 band per the step-count
  convention; the cross-step token-dependency graph adds depth, but the
  count-based tier is `medium` — see §15)
- `thesis:ttp_chain`
- `thesis:privilege_escalation`
- `thesis:cloud_provider_flaw`
- `thesis:misconfiguration`
- `vulnerability_disclosure` (the `vulnerability_story` in §3 is substantive)
- `mixed_reproducibility` (the dedicated coverage role of this blog)
- `patched_provider_flaw`

(The four required tags from the brief are present: `cloud:gcp`,
`platform:kubernetes`, `mixed_reproducibility`, and
`thesis:privilege_escalation` — with `thesis:vulnerability_chain`'s intent
carried by `thesis:cloud_provider_flaw` + `vulnerability_disclosure`.)

## 15. Manual ground-truth notes

**The highest-value section** — what a human walker noticed that an LLM
Extractor on the same blog might miss.

- **Reproducibility is the crux, and the easiest thing to get wrong.** The chain
  *looks* "mostly reproducible" to a naive reader because Steps 3 and 6 use
  plain `kubectl` and the CRAC self-escalation is a famous, still-working
  technique. **The trap:** the generic steps only execute because earlier steps
  leaked tokens/permissions via flaws Google **patched on 2023-12-14
  (GCP-2023-047)**. An Extractor that scores each step in isolation will likely
  mark Steps 2 and 4 `full` (they are "just" reading a file and using a token)
  and miss that the vulnerable provider-internal state no longer exists on
  managed GKE and **cannot be reverted in a lab**. The walker scored Steps 2 and
  4 `demonstration_only` on that provider-patch basis.

- **Alternative reading on Step 5 (deliberately rejected).** Step 5 (pod
  creation) was scored `demonstration_only` rather than `full` even though
  `kubectl create pod` is generic, because the **authority** that permits the
  creation is the patched ASM CNI excessive RBAC; on managed GKE post-patch the
  CNI token can no longer create that pod. A defensible alternative is
  Step 5 = `full` (the mechanical pod-create is reproducible *if* you grant an
  equivalent role). The walker rejected it to stay faithful to "reproduce the
  blog's chain as described," but flags it here. Note: the lab-level rollup is
  `mixed` under either reading, so this judgment does not change §13.

- **Step-boundary judgment.** The article is narrated as "two issues"
  (FluentBit, ASM) plus a CRAC finale, with a stipulated initial-access
  precondition. The walker split it into **6 ordered steps** to expose the
  heterogeneity cleanly: token-theft (Step 2) separated from
  token-use/enumeration (Step 3), and CNI-token-theft (Step 4) separated from
  pod-creation (Step 5) and CRAC escalation (Step 6). A coarser extractor may
  collapse these into ~3 steps (FluentBit leak / ASM pod / CRAC), which still
  yields `mixed` but **hides which sub-action is the patched part**. The fine
  split is the more useful ground truth.

- **Non-required precondition.** Step 1 (initial RCE/escape into FluentBit) is
  `not_reproducible` because the blog gives no concrete exploit. Per the
  any-heterogeneity rule, `not_reproducible` non-required steps are **excluded**,
  so they do not by themselves force a `not_reproducible` lab verdict; the
  `mixed` result comes from the *required* set spanning `full` +
  `demonstration_only`. A lab grants the FluentBit foothold directly. **Failure
  mode:** an Extractor that counts Step 1 as required-and-`not_reproducible`
  could wrongly push toward a `not_reproducible` lab verdict — that would be
  wrong here.

- **Verbatim / evidence gaps.** The CRAC pod manifest is shown only as an
  **image (Figure 6)**, not as copyable text, and the `kubectl` commands are
  referenced conceptually, not reproduced verbatim. So a lab must **reconstruct**
  the manifest and commands from prose, not copy them. The faithful
  reconstruction the prose supports is `serviceAccountName:
  clusterrole-aggregation-controller` plus a volume to persist the token — the
  walker did not invent manifest contents. This matters for any `full` claim
  that assumes a copy-pasteable artifact exists: it does not.

- **No CVE.** GCP-2023-047 is a Google **security bulletin**, not a CVE; the
  article assigns no CVE. `external_references.cves` is intentionally empty — do
  not hallucinate a `CVE-2023-xxxx`.

- **Platform / facet notes.** Target is GCP + Kubernetes, specifically GKE; the
  attacked surfaces are the GKE-bundled FluentBit DaemonSet and the Anthos
  Service Mesh add-on (**ASM must be installed for the chain to complete** — an
  explicit prerequisite). For runtime, `runtime:kubernetes` is the primary
  provision target and is **non-first-class** (outside
  `{aws, azure, gcp, github, local}`), flagged via `non_first_class_runtime`.
  `runtime:gcp` is included because the faithful target is managed GKE — but the
  patched flaws mean a current GKE provision **cannot** reproduce Steps 2/4, so
  `runtime:local` (kind/minikube) is also listed as the realistic substrate for
  *simulating* the misconfigs. A planner choosing pure `runtime:gcp` would find
  the vulnerable state unprovisionable.

- **MITRE mapping judgment.** Best-fit from the Containers/Kubernetes matrix:
  `T1552.001` (creds in files) for token reads, `T1528` (steal application
  access token) for the projected SA token, `T1613` (container/resource
  discovery) for enumeration, `T1610` (deploy container) for pod creation,
  `T1098.006` (additional container cluster roles) for the RBAC/CRAC
  manipulation, `T1078.004` (valid cloud accounts) for token impersonation,
  `T1611` (escape to host) / `T1190` (exploit public-facing app) for the
  stipulated initial access. The blog cites no ATT&CK IDs itself; treat these as
  analyst mapping, not source-stated.

- **Shape resolved to `vulnerability_disclosure` (ADR 0103).** The documented
  `shape` trio `{aws_ttp, supply_chain, incident_analysis}` fits none of this
  cleanly — it is a GCP/Kubernetes privilege-escalation vulnerability disclosure,
  not AWS and not an incident analysis. ADR 0103 found `shape` is purely
  descriptive and open-set (nothing branches on its value), so this walk and the
  manifest now both use `vulnerability_disclosure` directly instead of the earlier
  `aws_ttp` "closest fit." **The real platform signal lives in the `cloud:gcp` /
  `platform:kubernetes` coverage tags, not the `shape` token** — a consumer keying
  off `shape` to infer the cloud would be wrong here.

- **Phase 1 prompt-engineering hints.**
  1. The Extractor prompt must score reproducibility **with provider-patch
     awareness**: a step that uses generic tooling is *not* `full` if the
     vulnerable state it depends on was vendor-patched and cannot be reverted in
     a managed environment. Treat "vendor patched the precondition" as a
     reproducibility-downgrade signal.
  2. The prompt should distinguish **required** from **non-required
     precondition** steps so a `not_reproducible` initial-access stipulation
     does not over-pull the lab-level rollup toward `not_reproducible`.
  3. The prompt should not assume figures contain copyable artifacts —
     image-only manifests/commands require reconstruction, not extraction.
