# Blog walk: `<id>`

A manual ground-truth reading of a curated blog. The walker (a human) reads
the blog end-to-end and fills in each section below, mapping the blog's
narrative onto the AttackSpec structure (`docs/schema.md §4.8`). The walks
are the manual analog of what the Extractor will produce in Phase 1+; they
are the gold-standard reference Phase 1 prompt-engineering and eval calibrate
against.

How to use this template:

1. Copy this file to `dev/curated-blog-walks/<blog-id>.md`. Pick a slug that
   matches the entry in `eval/blog-sets/manifest.yaml` (the `walk:` path
   points at this file).
2. Replace `<id>` and `TBD` markers with real content from the blog. Section
   headings are stable; content under each section is yours.
3. Leave a section heading even if it doesn't apply, and write "Not
   applicable for this blog" with a one-sentence reason. Empty sections are
   harder to read than explicitly-empty ones.
4. The **Manual ground-truth notes** section at the bottom is where the
   high-value reading lives. Spend time on it.

---

## 1. Header

- **id:** `<blog-id>` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `aws_ttp` | `supply_chain` | `incident_analysis`
- **URL:** TBD
- **Canonical URL:** TBD (often the same; differs if the blog 301s, syndicates,
  or has a vendor-redirect)
- **Title:** TBD
- **Publisher:** TBD
- **Publisher kind:** `vendor_lab` | `researcher_personal` | `vendor_advisory`
  | `conference_writeup` | `other` (per `schema.md §4.8` line 312)
- **Authors:** TBD
- **Publication date:** TBD (ISO 8601)
- **Accessed date:** TBD (ISO 8601 — when the walker read it)
- **Content hash:** TBD (optional in walks; the Extractor produces this
  mechanically. Leave as TBD unless you have it.)

## 2. One-paragraph summary

What happened, in plain English. Three or four sentences. The reader of this
walk should know the shape of the attack without having to open the blog.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types** (one or more from the v1 seed list): `ttp_chain`,
  `vulnerability_chain`, `misconfiguration`, `cloud_provider_flaw`,
  `supply_chain_compromise`, `incident_analysis`, `cross_tenant_compromise`,
  `privilege_escalation`, `persistence_pattern`, `detection_methodology`.
- **Summary:** TBD
- **Attacker objective:** TBD
- **Vulnerability story:** TBD (substantive for vulnerability-disclosure
  blogs; may be empty for pure TTP-chain blogs — say so explicitly).

## 4. Chain steps

The ordered list of attacker actions the blog describes. One subsection per
step. Step shape mirrors `schema.md §4.8` lines 358–383.

### Chain step 1: `<title>`

- **Blog excerpt:**
  > TBD (a verbatim quote that anchors this step in the source text)
- **Description:** TBD
- **MITRE techniques:** [e.g., `T1078.004`, `T1195.002`]
- **Preconditions:** TBD
- **Postconditions:** TBD
- **Outputs (value-type names from `schema.md §4.12`):** [e.g.,
  `aws_credentials`, `aws_iam_role_arn`]
- **Reproducibility:** `full` | `partial_simulation` | `demonstration_only`
  | `not_reproducible`
- **Why this tier (not a higher one):** TBD

### Chain step 2: `<title>`

(repeat the step shape above; add as many subsections as the blog has steps)

## 5. Alternative paths (optional)

Per `schema.md §4.8` lines 387–401. Only fill in if the blog presents
alternative attack paths (e.g., "B2B trust hopping" in a B2B blog). If the
blog has a single canonical chain, write: "Not applicable for this blog —
the chain is canonical and unbranched."

## 6. Facets

Per `schema.md §4.13`. Walker's best estimate; categories tagged with who is
*architecturally* responsible for them (Extractor for blog-derived; Planner
for lab-derived). The walk records both for ground-truth comparison.

### 6.1 `target:*` (Extractor-derived)

What the attack targets. Examples: `target:aws`, `target:azure`, `target:gcp`,
`target:entra_id`, `target:aws_iam`, `target:github`, `target:github_actions`,
`target:npm_registry`.

### 6.2 `runtime:*` (Planner-derived; walker's guess)

What the lab *would* provision against. First-class in v1: `runtime:aws`,
`runtime:azure`, `runtime:gcp`, `runtime:github`. Best-effort: anything else
(walker may propose; flag explicitly as best-effort).

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

Examples: `lab_class_signal:incident_analysis`,
`lab_class_signal:vulnerability_chain`, `lab_class_signal:external_channel`,
`lab_class_signal:requires_infra`, `lab_class_signal:simulated_components`,
`lab_class_signal:multi_language`, `lab_class_signal:parameterized`,
`lab_class_signal:produces_world_state`, `lab_class_signal:expected_detections`,
`lab_class_signal:manual_prereq`, `lab_class_signal:time_marked`,
`lab_class_signal:waits_for_condition`.

## 7. Value types referenced

The set of value-type registry keys (per `schema.md §4.12`) the blog surfaces.
These should already exist in `registry/value_types.yaml`, or the walk
identifies a new type that registry-evolution will add. Examples:
`aws_credentials`, `github_pat`, `aws_iam_role_arn`, `npm_token`,
`disk_seeded_file`.

If the walk surfaces a value type that doesn't exist in the v1 registry, flag
it here with a `(proposed)` marker and a one-sentence justification.

## 8. Defender techniques

Per `schema.md §4.8` lines 403–414. Populate this section only for
`incident_analysis` blogs. For non-incident-analysis blogs, write: "Not
applicable for this blog — no defender techniques described in the source."

Per-technique shape:

- **Name:** TBD
- **Technique kind:** `investigation` | `detection_engineering` |
  `threat_hunting` | `forensic_analysis`
- **Applies to chain steps:** [list of chain-step IDs]
- **Description:** TBD

## 9. Defenses

Controls per `schema.md §4.8` lines 416–426. Per-defense shape:

- **Description:** TBD
- **Applicability:** `customer_actionable` | `architectural_mitigation` |
  `detection_only` | `vendor_only`
- **Addresses chain steps:** [list of chain-step IDs]
- **Detection path / format** (when applicable): TBD

## 10. External references

- **CVEs cited:** TBD
- **Related blogs:** TBD
- **MITRE ATT&CK techniques referenced:** TBD (techniques the blog cites
  beyond the ones used in chain steps; e.g., the blog might cite T1078 as
  background reading)

## 11. Real-world incidents

Per `schema.md §4.8` lines 339–356.

- **Status:** `unknown` | `none_observed` | `incidents_documented`
- **Evidence source:** TBD (required when status ≠ unknown)
- **Incidents** (when status == `incidents_documented`): per-incident
  `name`, `description`, `affected_organizations`, `attribution`,
  `date_range`.

## 12. Expected lab class

The walker's prediction of how Phase 1's Extractor + Planner should classify
this lab. Two parts:

- **Lab kind:** e.g., "AWS IAM privilege-escalation TTP chain";
  "GitHub Actions supply-chain compromise"; "Entra ID incident-analysis
  walkthrough".
- **Why this class:** TBD (one paragraph linking the facets in §6 to the
  classification).

## 13. Reproducibility (lab-level)

Derived from the per-step `reproducibility` values in §4. The any-
heterogeneity-mixed rule (`schema.md §4.8` line 438): if all required steps
share a tier, take that tier; if required steps span tiers, the lab is `mixed`.

- **Classification (lab-level):** `full` | `partial_simulation` |
  `demonstration_only` | `not_reproducible` | `mixed`
- **Caveats:** TBD (e.g., "9 of 10 phases are full; 1 phase is
  demonstration_only because it involves a destructive payload that cannot
  be safely executed in a lab")
- **Derivation trace:** TBD (which steps' tiers led to the classification)
- **Overall assessment:** TBD (one sentence)

## 14. Coverage tags

Which `eval.md §7.3` coverage dimensions this blog satisfies. Drives the
`coverage_tags:` field in `eval/blog-sets/manifest.yaml`. Suggested format:
short string tags. Examples:

- `cloud:aws`, `cloud:azure`, `cloud:gcp`, `platform:github`
- `multi_platform` (when the lab touches two or more)
- `complexity:simple` (≤3 chain steps), `complexity:medium` (4–8),
  `complexity:complex` (9+)
- `thesis:ttp_chain`, `thesis:vulnerability_chain`,
  `thesis:supply_chain_compromise`, etc. (from §3 of this walk)
- `lab_class_signal:<name>` (from §6.3 of this walk)
- `non_first_class_runtime` (when the lab would target a best-effort runtime
  per `schema.md §4.13`)
- `incident_analysis` (when defender techniques are present)
- `vulnerability_disclosure` (when `vulnerability_story` is substantive)

A single blog typically carries 5–10 tags; coverage is overlapping by design
(see `eval.md §7.3` line 57).

## 15. Manual ground-truth notes

**The highest-value section.** What the walker (a human) noticed that an LLM
Extractor running on the same blog might miss. This is what makes the walk
worth doing.

Suggested subsections:

- **Ambiguous wording in the blog:** what reading the walker picked, and why.
- **Missing information:** what the blog elides, what the walker had to infer
  or look up elsewhere.
- **Alternative readings:** sentences where another interpretation is
  defensible, and what the walker rejected.
- **Calls the walker made:** judgment calls on facet selection, value-type
  mapping, reproducibility classification — anything the architecture leaves
  to the operator.
- **Failure modes to watch:** patterns in this blog that are likely to fool an
  LLM Extractor (e.g., a passive-voice description of a credential exchange
  that obscures who possesses what).
- **Phase 1 prompt-engineering hints:** if the walker spots a pattern the
  Extractor prompt should explicitly handle (e.g., "the blog uses 'the
  attacker' and 'the user' interchangeably — disambiguation prompt needed").
