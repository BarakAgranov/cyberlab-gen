# Blog walk: `long-multi-stage-cloud-campaign`

A manual ground-truth reading of a **synthetic long-form** curated blog,
included to exercise the Extractor's chunking on a large input
(`implementation-plan.md §4.6` long-blog risk; Task-8 brief "include at least
one long blog"). Unlike the two real walks, this entry has no live URL: it is a
fixture whose purpose is to surface chunk-boundary handling, not to test
fidelity to a published source. The narrative below is representative of a
real long AWS TTP write-up (12 chain steps, ~6k words) so the chunking and
cross-chunk reference-resolution paths are genuinely exercised.

---

## 1. Header

- **id:** `long-multi-stage-cloud-campaign` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `aws_ttp`
- **URL:** TBD (synthetic fixture; no published source)
- **Canonical URL:** TBD
- **Title:** "Long-form multi-stage cloud campaign (chunking exercise)"
- **Publisher:** TBD (synthetic)
- **Publisher kind:** `other` (synthetic fixture)
- **Authors:** Task-8 eval-harness author (synthetic)
- **Publication date:** 2026-06-01
- **Accessed date:** 2026-06-01
- **Content hash:** TBD (the Extractor produces this mechanically)

## 2. One-paragraph summary

A long TTP write-up reconstructing a twelve-step AWS campaign: initial access
via a leaked CI service-account key, reconnaissance across STS / IAM / S3,
privilege escalation through a permissive `iam:PassRole`, lateral movement into
a second account via a cross-account trust, persistence via a new IAM user and
an EventBridge-triggered Lambda, collection of secrets from SSM and Secrets
Manager, exfiltration to an attacker-controlled bucket, and anti-forensics by
disabling a CloudTrail trail. The write-up's length and the way it revisits the
same role ARNs across distant sections is exactly what makes it a chunking
exercise: a naive single-pass read drops the cross-references between the
escalation step (chunk 1) and the persistence step (chunk 3).

## 3. Thesis

- **Types:** `ttp_chain`, `privilege_escalation`, `persistence_pattern`.
- **Summary:** A multi-account AWS compromise chaining a leaked CI key into
  full administrative persistence across two accounts.
- **Attacker objective:** Durable cross-account administrative access plus
  staged exfiltration of secrets.
- **Vulnerability story:** Empty — this is a pure TTP-chain write-up of
  misconfiguration abuse, not a single-vulnerability disclosure.

## 4. Chain steps

Twelve steps; abbreviated here to titles, tiers, and the cross-chunk
references that make this a chunking test. Full per-step excerpts live in the
fixture body the harness feeds the Extractor.

### Chain step 1: leaked CI service-account key (initial access)
- **MITRE techniques:** `T1078.004`
- **Reproducibility:** `full` — scriptable with a seeded leaked key.

### Chain step 2: STS / IAM / S3 reconnaissance
- **MITRE techniques:** `T1580`
- **Reproducibility:** `full`

### Chain step 3: enumerate role trust policies
- **MITRE techniques:** `T1580`
- **Reproducibility:** `full`

### Chain step 4: privilege escalation via `iam:PassRole`
- **MITRE techniques:** `T1548`
- **Reproducibility:** `full` — the escalated role ARN is referenced again in
  step 9 (cross-chunk reference #1).

### Chain step 5: assume escalated role
- **MITRE techniques:** `T1550.001`
- **Reproducibility:** `full`

### Chain step 6: cross-account trust hop (lateral movement)
- **MITRE techniques:** `T1199`
- **Reproducibility:** `partial_simulation` — the second account is simulated
  in the lab (cross-account is not safely reproducible against a single tenant).

### Chain step 7: create backdoor IAM user (persistence)
- **MITRE techniques:** `T1136.003`
- **Reproducibility:** `full`

### Chain step 8: EventBridge-triggered Lambda persistence
- **MITRE techniques:** `T1546`
- **Reproducibility:** `full`

### Chain step 9: collect SSM parameters + Secrets Manager secrets
- **MITRE techniques:** `T1552.005`
- **Reproducibility:** `full` — re-uses the escalated role ARN from step 4
  (cross-chunk reference #1 resolves here).

### Chain step 10: stage exfiltration bundle
- **MITRE techniques:** `T1074.001`
- **Reproducibility:** `full`

### Chain step 11: exfiltrate to attacker bucket
- **MITRE techniques:** `T1537`
- **Reproducibility:** `demonstration_only` — real exfil to an external bucket
  is not performed in the lab; the step is demonstrated, not executed.

### Chain step 12: disable CloudTrail trail (anti-forensics)
- **MITRE techniques:** `T1562.008`
- **Reproducibility:** `full`

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched (the
cross-account hop in step 6 is part of the canonical chain, not an alternative).

## 6. Facets

### 6.1 `target:*` (Extractor-derived)
`target:aws`, `target:aws_iam`

### 6.2 `runtime:*` (Planner-derived; walker's guess)
`runtime:aws` (first-class).

### 6.3 `lab_class_signal:*`
`lab_class_signal:requires_infra`, `lab_class_signal:simulated_components`
(the second account in step 6), `lab_class_signal:produces_world_state`,
`lab_class_signal:expected_detections` (step 12 disables a trail).

## 7. Value types referenced

`aws_credentials`, `aws_iam_role_arn`, `aws_iam_user`, `aws_secret`,
`aws_ssm_parameter`, `aws_s3_bucket`. All expected to exist in
`registry/value_types.yaml`; none flagged `(proposed)`.

## 8. Defender techniques

Not applicable for this blog — it is a pure attacker-perspective TTP chain with
no defender-investigation narrative.

## 9. Defenses

- **Description:** Scope `iam:PassRole` with a `Condition` restricting the
  passable role; alert on `iam:CreateUser` and `cloudtrail:StopLogging`.
- **Applicability:** `customer_actionable`
- **Addresses chain steps:** steps 4, 7, 12.

## 10. External references

- **CVEs cited:** none (misconfiguration abuse, no CVE).
- **Related blogs:** none.
- **MITRE ATT&CK techniques referenced:** the per-step techniques in §4.

## 11. Real-world incidents

- **Status:** `none_observed`
- **Evidence source:** Synthetic fixture — no real incident is claimed.

## 12. Expected lab class

- **Lab kind:** "AWS multi-account IAM privilege-escalation + persistence TTP chain."
- **Why this class:** `target:aws` + `target:aws_iam` with `requires_infra` and
  `simulated_components` (the second account) point at a multi-phase AWS lab
  whose cross-account hop is simulated, not provisioned against two real tenants.

## 13. Reproducibility (lab-level)

- **Classification (lab-level):** `mixed`
- **Caveats:** 10 of 12 steps are `full`; step 6 is `partial_simulation`
  (simulated second account) and step 11 is `demonstration_only` (no real
  exfil). Per the any-heterogeneity-mixed rule the lab is `mixed`.
- **Derivation trace:** steps 1–5,7–10,12 = `full`; step 6 = `partial_simulation`;
  step 11 = `demonstration_only` → heterogeneous → `mixed`.
- **Overall assessment:** A largely reproducible AWS chain with two
  intentionally-bounded steps.

## 14. Coverage tags

`cloud:aws`, `shape:aws_ttp`, `complexity:complex`, `long_blog:chunking`,
`thesis:ttp_chain`, `lab_class_signal:simulated_components`.

## 15. Manual ground-truth notes

- **Why this is a chunking test:** the escalated role ARN introduced in step 4
  is re-referenced in step 9, and the second-account ID from step 6 reappears in
  steps 7–8. If the Extractor reads the blog in independent chunks without
  carrying forward earlier entities, the `depends_on` edges between distant steps
  (4→9, 6→7) get dropped — that omission is the failure mode this fixture exists
  to catch.
- **Calls the walker made:** step 6 is `partial_simulation` (not
  `not_reproducible`) because the cross-account trust *mechanism* is reproducible
  against a simulated second account even though a real second tenant is not
  provisioned.
- **Failure modes to watch:** the long input is likely to push the Extractor's
  token budget; if completeness drops sharply on this blog relative to the two
  short walks, that is the chunking/budget signal the eval harness should flag
  (`implementation-plan.md §4.6`).
- **Phase 1 prompt-engineering hint:** the prompt should instruct the Extractor
  to maintain an entity table (role ARNs, account IDs, user names) across chunks
  so cross-chunk `depends_on` edges survive.
