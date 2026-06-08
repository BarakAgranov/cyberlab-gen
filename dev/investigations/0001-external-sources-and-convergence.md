# 0001 — External sources are tools, not registries; and the structural-retry burn

**Date:** 2026-06-08
**Status:** Findings captured. No code/contract changed by this document. The fixes it
recommends are pending and sign-off-gated (they touch `validation.md §6.4`).
**Settled principle it feeds:** ADR 0055 (external sources are tool adapters, not
proposable registries; unverifiable-but-well-formed identifiers never hard-fail).
**Provenance:** reconstructed this session from the run store, the local Phoenix trace,
and a read of the source. Each claim is tagged `[V]` (verified in an artifact/code/trace
I read) or `[I]` (inferred). Nothing was changed; this is a capture so future sessions and
fix-prompts can reference a repo document instead of re-deriving the investigation.

## Why this exists

A real `--auto extract` on the Wiz CodeBuild blog
(`https://www.wiz.io/blog/wiz-research-codebreach-vulnerability-aws-codebuild`) ran 6 LLM
calls, spent **$7.40**, and exited `interrupted` without shipping. This document reconstructs
exactly what happened, names the two category errors that caused it, records the honest
*negative* result of the wider hunt (it narrowed to essentially one mis-build, not many),
and splits the fixes into a cheap NOW and a real LATER.

---

## 1. The run, reconstructed

**Evidence base.** `~/.cyberlab-gen/runs/20260607T211616Z-…codebreach…/` (`run.json`,
`cost.yaml`, `checkpoint.sqlite`); the local Phoenix trace (project `cyberlab-gen`, windowed
to 21:16–21:43 UTC).

- **Frame `[V]`.** Started `21:16:16Z`, ended `21:42:52Z`, `status: interrupted`,
  `halt_reason: interrupted`. `num_llm_calls: 6`, `total_cost_usd: 7.397840`. Every
  `cost.yaml` entry is `agent_label: extractor` → **zero jury calls**. No
  `spec.yaml`/`jury-verdict.yaml`/`enrichment.yaml` in the run dir → it **never shipped**.
- **A1 / refinement never exercised `[V]`.** 0 jury calls; the checkpoint never carried a
  `REFINEMENT` route or `refinement_iterations > 0`. `Extractor.refine` is the jury-`revise`
  path only (ADR 0054; `framework/refinement.py`). This run was **entirely the static-schema
  STRUCTURAL_RETRY path** (`validation.md §6.10`, full re-extraction).
- **6 calls = 3 + 3 across two extracts `[V]`.** `extract()` runs an internal loop of
  `max_attempts = 1 + hallucination_retry_attempts = 3` (`extractor.py:166`) and **returns
  early the instant checks pass** (`extractor.py:190`). The checkpoint `structural_attempts`
  trajectory is `0 → 1 → 1 → 2 → 2` → exactly **two** `extract_node` executions. Two extracts,
  each ≤ 3 internal attempts, summing to 6 admits only **3 + 3** (forced, not inferred).
  Corroborated by the `cost.yaml` token curve: entries 1–3 ≈ 78–79K input (no structural
  feedback yet); entries 4–6 = 114K/171K/101K (structural feedback + accumulated `propose_*`
  tool history).
- **The cost driver is `_check_mitre` `[V, by elimination]`.** Because the loop returns early
  on empty findings yet ran all three internal attempts in each extract, findings fired on
  attempts 1–2 of both. Of the three framework checks in `_run_checks` (`extractor.py:384`):
  `_check_cves` returns `[]` immediately (`nvd_client is None`, `extractor.py:450`);
  `_check_search_before_claim` only fires on `external_api`-sourced CVE numeric fields
  (`extractor.py:403-409`), and the spec's external refs were **advisories, not external_api
  CVEs**; `_check_mitre` (`extractor.py:422`) fires on any technique id absent from the bundled
  catalog. So MITRE is the only check that *can* fire here — it **necessarily** drove the
  internal re-prompts (4 of the 6 calls). The Phoenix re-prompt text (this session's pull)
  names the flagged ids: **T1593, T1526, T1136.003** — all real, current ATT&CK ids `[V]`.
- **Findings cleared 8 → 1 `[V]`.** Checkpoint step 2 (validate #1): 8 static-schema findings
  (5 `unknown_facet`, 2 `unknown_thesis_type`, 1 `unknown_external_source @
  advisories[0].source = 'aws'`); extract #1 emitted **no proposals**. Checkpoint step 3
  (extract #2): the model emits proposals (facets ×5, thesis ×2, value_type ×1) and remaps
  `advisories[0].source 'aws' → 'vendor_site'` (and over-corrects by dropping the valid
  `target:aws` facet). Checkpoint step 4 (validate #2): provisional resolution consumes the
  proposals → the 7 facet/thesis findings clear, leaving **1** survivor:
  `unknown_external_source @ advisories[0].source` (`'vendor_site'` also unregistered — the
  registry holds only `nvd`). So the proposable-vocabulary machinery **works**; only the
  non-proposable external-source finding persists.
- **Bounded, interrupted one extract short of the halt `[V]/[I]`.** `structural_attempts`
  `0→1→1→2→2`, cap is 3 (`DEFAULT_STRUCTURAL_RETRY_ATTEMPTS`); the interrupt landed during
  setup of extract #3 `[V]`. Had it not, extract #3 → `structural_attempts = 3` → validate #3
  evaluates `3 < 3 == False` → **`HALTED_VALIDATION`** on the one unconvergeable finding `[I,
  from orchestrator logic]`. So the loop is **bounded** (cap 3, well under the $25 ceiling) but
  **wasteful**: extract #3 would have spent ~3 more MITRE-thrashing calls (~+$3–4) to reach a
  halt on a finding that can never clear. The "won't terminate" feel was the interrupt landing
  just before the halt.

---

## 2. The two confirmed category errors

**① `_check_mitre` hard-rejects real techniques against an 8-entry seed catalog.**
`_check_mitre` (`extractor.py:422-444`) loads `registry/mitre_attack_techniques.yaml`
(**8 entries** `[V]`: T1078, T1078.004, T1098, T1190, T1530, T1552, T1552.005, T1580) and
flags any technique not in it as `mitre_hallucination`. The blog's central techniques —
**T1195 Supply Chain Compromise, T1199 Trusted Relationship**, plus T1593/T1526/T1136.003 —
are real and absent, so correct extraction is rejected as hallucination. The file's own header
calls it "a *seed* subset… NOT a live mirror… grows by maintainer PR." This drove ~⅔ of the
$7.40. The tool layer confirms the design intent was a local gate, not a tool:
`tools.py:106-108` tells the model *"there is no 'mitre' / 'mitre_attack' source; technique ids
are validated automatically against the bundled MITRE catalog."*

**② `AdvisoryReference.source` (a provenance label) validated against the tool catalog.**
`_check_external_sources` (`static_schema_validator.py:306-317`) resolves
`AdvisoryReference.source` (typed `ExternalDataSourceId`, `attack_spec.py:273`;
`ExternalDataSourceId = SnakeName`, `primitives.py:69`) against the `external_data_sources`
registry — whose entire contents are `['nvd']` `[V]`. But `advisory.source` is a **publisher
provenance label** (*who published the advisory* — AWS), not a queryable tool id. So a correct
`source: aws` can never resolve. This is the **lone unconvergeable finding** that blocked
shipping.

---

## 3. The per-`_check_*` enumeration — the honest narrowing

I enumerated **every** `_check_*` in the codebase (extractor, jury, validator). The wider hunt
for "more MITRE-style cases" narrows to **exactly one** true tool-check-as-registry mis-build
(①) plus **one** adjacent content-field conflation (②). That negative result is itself
valuable: it means the fix surface is small and well-bounded.

**The split is already structural in the schema `[V]`.** `registries.py:119` defines
`_ExternalSourceEntryBase` (an HTTP adapter: `base_url`, `auth_type`, `endpoints`,
`rate_limit`, `cache`), and **both** `ExternalDataSourceEntry` (`:153`, enrichment-triggered)
and `StaticCatalogEntry` (`:168`, "consulted on-demand by the Generator and Validator, e.g.
`lookup_cloud_iam_action`; never enrichment-triggered") inherit it. The code's own schema
treats external sources as **queryable adapters**, in two flavors. Neither is a controlled
vocabulary. The conflation lives only in two checks and some prose.

### Confirmed mis-builds

| # | Check (file:line) | What's wrong | Better design |
|---|---|---|---|
| ① | `_check_mitre` (`extractor.py:422`); `mitre_attack_techniques.yaml`; `tools.py:106-108` | external-authority data hard-checked against an 8-entry local seed; no tool path; no graceful skip | the `_check_cves` pattern: no membership gate, pass well-formed `T####(.###)` ids through as unverified (`requires external research`), verify/fetch via a wired adapter when present |
| ② | `_check_external_sources` (`static_schema_validator.py:287-317`); `attack_spec.py:273` | a publisher label validated against the queryable-tool registry (`['nvd']`) | retype `AdvisoryReference.source` off `ExternalDataSourceId` to a free publisher label; drop `adv.source` from the check |

### Searched and CLEARED (the architect's frame deliberately does *not* fit these) `[V]`

- **`_check_closed_catalog_membership` (`static_schema_validator.py:320`) — legitimately
  local.** It checks `severity_levels`, `detection_components`, `detection_formats`,
  `provisioning_mechanisms` against their bundled catalogs, but these are **project-owned
  closed enums**; the docstring says the check guards enum-vs-catalog **drift** (`CATALOG_DRIFT`
  = our own catalog is stale vs our own enum). Both sides are project-controlled. Internal
  consistency, **not** an external-authority lookup. Leave alone.
- **GitHub repos / packages — named in the prompt, but already degrade.** `prompt.md:60-61`
  lists "GitHub repos, packages" in search-before-claim, but there is **no
  `_check_github`/`_check_package`**, **no local list**, and **no wired source** — so
  `external_lookup(source_id='github')` hits the graceful "unavailable → `unknown_from_blog` /
  `requires external research`" path (`tools.py:234-253`). They already behave like the `nvd`
  pattern. The only defect is a **prompt over-promise** (telling the model to look up sources
  that can't be served → wasted calls). Minor.
- **`facets` / `thesis_types` (`_check_facets:228`, `_check_thesis_types:254`) — project
  vocabulary; correctly local + proposable.** Resolve against project-owned registries with a
  working provisional-resolution/proposal path (proven this run, 8→1). Legit.
- **`static_catalogs: aws_iam_catalog` — correctly tool-shaped, unwired; not checked in
  Phase 1.** `static_catalogs.yaml` frames it as a downloadable asset consulted via
  `lookup_cloud_iam_action`; no Phase-1 `_check_*` touches it. No action.

### Per external source `[V]`

| Source | In `external_data_sources` registry? | Adapter code? | Used as | Verdict |
|---|---|---|---|---|
| **nvd** | Yes (full adapter spec) | **No** — `cyberlab_gen/external_data_sources/` **does not exist**; `nvd_client is None` | `external_lookup('nvd')` → degrades gracefully (`tools.py:263-272`); `_check_cves` **skips** when client is None (`extractor.py:450`) | **Correct tool pattern**, unwired. The reference design. |
| **mitre_attack** | **No** — implemented outside the registry as a bare bundled catalog, classed "with the closed catalogs of ADR 0016" (`loader.py:185-191`) | n/a | `_check_mitre` hard-reject; `external_lookup` explicitly refuses mitre | **MIS-BUILT as a local registry + hard-reject gate.** |
| **osv** | **No** — absent entirely | No | unreferenced in code | **Unbuilt** (the LATER build). |

**No `external_data_sources/<id>/` adapter code exists for any source** (nvd included) `[V]`. The
registry is today a *catalog of intended tools, none wired*. That is fine for the architecture —
which is exactly why the one thing that *hard-fails* instead of degrading (MITRE) stands out.

---

## 4. The graceful-vs-hard-fail asymmetry

Same condition — *authoritative source unavailable / id not in our local thing* — handled two
opposite ways `[V]`:

- **Degrades (correct):** `external_lookup` against an unavailable/unwired/unknown source is
  never fatal (`tools.py:234-272`, ADR 0042, `is_error=False`); `_check_cves` skips when
  `nvd_client is None` (`extractor.py:450`); search-before-claim steers to `unknown_from_blog`
  (`prompt.md:45-47`).
- **Hard-fails (wrong):** `_check_mitre` (against an incomplete seed) and `advisory.source`
  (against the tool registry).

**The reference pattern that is already CORRECT in the codebase is `nvd` + `_check_cves`:** no
local list, graceful skip when the verifier is unwired. Every mis-built check should be made to
behave like it.

---

## 5. NOW vs LATER per fix

| Item | NOW (cheap; unblocks shipping / stops the burn; no adapter wiring) | LATER (Phase 2 adapter build) |
|---|---|---|
| **① MITRE** | Ungate `_check_mitre` to behave like `_check_cves`: keep only the `T####` **format** check; pass well-formed-but-uncatalogued ids through flagged `requires external research`; remove the prompt's "hallucination… rejected" threat for MITRE and tell the model to cite blog-named techniques, else mark unknown. *Kills the dominant cost driver immediately.* | Wire `external_data_sources/mitre_attack/` with `lookup_by_id` + `lookup_by_description` (so the extractor can **fetch** the right technique when the blog describes but doesn't name one); make `external_lookup` serve it; fix `tools.py:106` + `enrichment.py`. |
| **② advisory.source** | Drop `adv.source` from `_check_external_sources` (or make it report-only); optionally retype the field now. *Clears the lone unconvergeable finding.* | Model advisory provenance as a publisher label/enum; if verification is wanted, wire advisory-source adapters (AWS bulletins / MSRC). |
| **②′ cve.source_of_record** | Validate only **post-enrichment** (it is framework-authored — `attack_spec.py:250`, set by `enrichment.py:482`); never at the structural gate. Latent; low urgency. | Same NVD adapter wiring as ①'s story. |
| **GitHub / packages** | Fix the prompt: don't mandate looking up sources that can't be served this phase. | Wire GitHub API + package-registry (npm/PyPI/OSV) adapters. |
| **closed-enum drift guard** | **Leave as-is** — legitimately local. | — |

---

## 6. Doc / ADR cleanup + reconciliation

**Docs that mis-frame `external_data_sources` as a proposable vocabulary/registry** (the
**keeper is `schema.md §4.14`** + the structural split in `registries.py`):

- `schema.md §4.16` (~line 799: proposal-authority-by-registry list includes
  `external_data_sources`; ~line 906: "three first-class registries — value_types, facets,
  external_data_sources — … evolvable through PR workflow or LLM-proposed entries").
- `validation.md §6.4` (~line 51: groups `external_data_sources` with `value_types`/`facets` as
  registry references the spec must resolve into).
- ADR **0044** ("external_data_sources stays maintainer-PR-only" — true clause, **wrong
  reason**: it needs *adapter code*, not vocabulary review).
- ADR **0050 / E1** (carries the proposal-authority list including `external_data_sources`).

**MITRE is documented both ways — the docs already partly support the tool framing:**

- *Pro-tool (correct):* `agents.md:79` (MITRE reachable via `external_lookup`);
  `registry-details.md:1440` (`mitre_attack` as an `external_data_sources` entry);
  `schema.md:708` & `implementation-plan.md:271` (MITRE = "static JSON" external source);
  `registry-details.md:2078` ("the bundled MITRE reference… is downloaded JSON, not authored
  content"). `enrichment.py:106-109` calls MITRE's locality a Phase-1 stopgap *"when MITRE
  becomes a live source"*, and `_MITRE_SOURCE_ID` exists at `enrichment.py:83`.
- *Pro-local (documents the as-built deviation; fix toward the tool model):* `pipeline.md:78`
  ("Technique IDs are validated against the bundled MITRE ATT&CK reference; CVE references… via
  the external_data_sources registry").
- *Drift to fix:* `mitre_attack_techniques.yaml` header claims it's "Referenced by
  `static_catalogs` entry `mitre_attack_techniques` (… -> entries_ref)" — **no such
  static_catalogs entry and no `entries_ref` field exist**; and `registry-details.md:2078` cites
  path `registry/mitre-attack/`, but the real file is `registry/mitre_attack_techniques.yaml`.

**MITRE-as-closed-catalog mis-classing:** `loader.py:185-191` groups MITRE "with the closed
catalogs of ADR 0016." ADR 0016's closed catalogs are **project-owned enums**; MITRE is
**external-authority data**. The grouping is the seed of the category error.

**Typing:** retype `AdvisoryReference.source` off `ExternalDataSourceId` (`attack_spec.py:273`).
Keep `CveReference.source_of_record: ExternalDataSourceId | None` (`attack_spec.py:258`) —
coherent as a verifying-tool id — but only validate it post-enrichment.

**Loaded-vs-documented reconciliation `[V]`:** documented v1 `external_data_sources` =
`{nvd, mitre_attack, osv}` (+ `static_catalogs: aws_iam_catalog`); **loaded = `{nvd}`** only;
`mitre_attack` is implemented outside the registry as a bundled catalog + hard-reject gate (the
misbuild); `osv` is absent entirely. No adapter code exists for any source.

**Withdrawn:** the prior-session "make the model remap/drop `aws`" fix proposal is **WITHDRAWN**
— it would degrade correct extraction to satisfy a category-confused rule. The run already shows
the model doing exactly that (`aws → vendor_site`) and still failing. The fix belongs in
schema/validator/docs, not in model behavior.

---

## Recent decisions/work built on the conflation — revisit
- ADR **0044** and ADR **0050/E1** (treat `external_data_sources` as a maintainer-evolved
  *vocabulary* on the proposal axis; it's a *tool catalog* on a different axis).
- The **MITRE-as-closed-catalog classing** (`loader.py:188-191`, grouping with ADR 0016).
- The prior-session **"remap/drop `aws`"** proposal — withdrawn (above).

See ADR 0055 for the settled principle that governs the fixes.
