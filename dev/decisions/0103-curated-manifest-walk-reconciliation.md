# 0103 — Curated-set manifest ↔ finalized-walk reconciliation; `shape` is descriptive

**Date:** 2026-06-19
**Phase:** 2 (v0.3 exit, step 3 — post-Task-10 reconciliation)
**Architecture refs:** `eval.md §7.3` (blog-set composition + coverage tagging — the home of the
coverage-tag namespaces), `schema.md §4.13` (the `target:*` / `runtime:*` facet namespaces;
`platform:*` is *not* one of them), `schema.md §4.8` (chain-step shape — the step-count basis for
`complexity`), ADR 0014 (the blog-set-manifest schema; `shape` declared "open-set, plain string,
not an enum"), ADR 0025 (the harness loader), ADR 0102 (Task-10 curated growth, which introduced
the drift this ADR reconciles), ADR 0084 (docs edits are owned but must be surfaced + recorded).

## Context

Before the architect's paid `--stage plan` calibration run, the curated-set manifest
(`eval/blog-sets/manifest.yaml`) must be gold-standard-consistent with the **finalized** walks
(the 8 walks after their human ground-truth pass + the prior source-verification corrections). It
was not: the manifest was authored independently of the walks' §1 headers and §14 coverage tags, so
`shape`, `complexity`, and `coverage_tags` had drifted. `tests/eval/test_manifest.py` proved every
`walk:` path resolves but never checked that the *content* agreed with the walk it points at.

This reconciliation forced a decision the brief flagged as load-bearing: **is `shape` consumed by
the deterministic pipeline?** That answer decides whether the loose-trio-with-no-vocabulary is a
latent correctness gap (→ needs governance) or merely a descriptive label (→ a registry would be
over-engineering today).

## Finding: `shape` is purely descriptive — nothing branches on its value

Exhaustive search (CLAUDE.md: confirm an absence by search, not green tests):

- `BlogEntry.shape` is typed `str` (`eval/runner/manifest.py:52`), explicitly "open-set … not an
  enum" (ADR 0014). Adding a new value cannot break validation.
- **No consumer branches on the value.** Across `eval/runner/*` the only occurrences of `shape` are
  the field definition, the YAML data, and the word "shape" in docstrings. No `entry.shape == …`
  dispatch, no lookup keyed on it. The single test reference pins the long-blog's value
  (`test_all_ids_and_entry_lookup`) — a pin, not a branch.
- The deterministic **pipeline** (`cyberlab_gen/`) never imports `BlogEntry` at all; it operates on
  `AttackSpec` / `LabManifest`. `BlogEntry.shape` is an eval-harness label, not a pipeline input.
- Corroborating signal the brief predicted: the manifest already duplicated `shape` as **both** a
  `shape:` field and a `shape:*` coverage tag — redundancy only tolerable because nothing reads it.

**Conclusion:** `shape` is a descriptive label. A registry / closed enum for it would be machinery
for its own sake. The canonical-vocabulary question is **deferred** until a real consumer needs one
(e.g. a future stage that routes on blog shape) — recorded here so it is not re-litigated.

## Decision

1. **`shape` — descriptive, reconciled one-value-per-entry, no registry.** Each entry carries one
   `shape`, identical in the walk §1 and the manifest. The three cloud-provider-flaw disclosures
   that previously used a trio token "under protest" (`entra-id`, `confusedfunction`, `gke-fluentbit`
   — all were `aws_ttp`/`incident_analysis` least-bad fits) now use the honest descriptive value
   **`vulnerability_disclosure`** in both places. **`netlify` keeps `supply_chain`** — a deliberate
   deviation from the brief's "vulnerability_disclosure for the four": its walk §1/§15 argue
   `supply_chain` as the headline (the bug ships in a platform library consumed transitively → the
   blast radius is a supply-chain property), it is a documented-trio value, and reconciling the
   *manifest to the walk* is the right direction. Net new shape vocabulary introduced: exactly one
   (`vulnerability_disclosure`, used ×3) — the brief's "one new value, not three" goal holds.
   The redundant **`shape:*` coverage tag is dropped** from the manifest index and from the lone
   walk (`long-multi-stage`) that carried one; `shape` lives only in the `shape:` field + walk §1.

2. **`complexity` — by step-count convention, derived from §4 of the finalized walk.** Convention
   (already stated in the manifest comments): `medium` = 4–8 §4 chain steps, `complex` = 9+. Real
   counts (verified by counting `### Chain step` headers): ai-assisted 8, codebuild 11, confused-
   function 6, gke 6, netlify 5, entra 7, lucr-3 10, long 12. Corrections applied so walk §14 and
   manifest agree: **manifest** ai-assisted `complex→medium`, codebuild `medium→complex`, entra
   `complex→medium` (the manifest's own inline comment already said "7 chain steps"); **walk §14**
   gke `complex→medium` (6 steps is squarely `medium`; the walk's "fine-split justifies complex"
   argument contradicted the count convention and was demoted to a note). ai-assisted/codebuild were
   genuinely *inverted*, exactly as the brief suspected. No change to the convention itself.

3. **`coverage_tags` — a disciplined verbatim subset of the walk's §14, enforced.** The manifest's
   per-entry tags are the eval harness's *index* — every tag must appear **verbatim** (same string,
   same namespace) in that walk's §14. `test_manifest_coverage_tags_subset_of_walk_section_14`
   enforces `manifest ⊆ §14` for every entry; `test_manifest_shape_field_matches_walk_section_1`
   enforces the §1↔manifest shape agreement. Drift fixed in passing: `target:gke → platform:gke`
   (the brief's example), `vulnerability_disclosure:present → vulnerability_disclosure` (the walks'
   spelling; the four-dimension test updated to match), `incident_analysis:present → incident_analysis`,
   dropped `defender_techniques:present` / `target:nextjs` / `target:gcp_cloud_functions` (none in
   their §14). The §14 parser strips parentheticals and "not applied"/negated mentions so a *negated*
   tag ("No `multi_platform`") or an aside ("see `eval.md §7.3`") can't false-pass.

4. **Namespaces documented (root cause of #3).** `target:*` = the attack surface the Extractor is
   scored on (blog-derived **facet**, `schema.md §4.13`); `runtime:*` = what the Planner provisions
   (lab-derived facet); `platform:*` = a set-level **eval-coverage** label (`eval.md §7.3`), **not a
   facet** (it appears nowhere in §4.13, and the walks use it only in §14, never in §6). This is why
   `target:gke` (facet) and `platform:kubernetes`/`platform:gke` (coverage) legitimately coexist —
   they are different systems doing different jobs. Documented in `eval.md §7.3` (new "Coverage-tag
   namespaces" note) with a one-line pointer added to `schema.md §4.13`.

5. **`url:` confirmed canonical per entry; netlify verified.** netlify's `url:` (the Netlify vendor
   advisory) was checked against the walk excerpts: it contains the core verbatim excerpt
   ("manipulate the `X-Forwarded-Proto` header …") and covers all five legs, and it is the source
   the walk §1 is built on. GHSA-9jjv-524m-jm98 (which carries CVE-2022-39239) and Sam Curry's
   write-up corroborate and are recorded in the walk §10/§15 — but the canonical source is the
   Netlify blog. **No URL change.** The only fix was aligning the `publisher:` string between walk §1
   ("Netlify") and the manifest (was "Netlify (with GHSA + researcher corroboration)").

## Consequences

- The manifest is now provably consistent with the finalized walks: shape/complexity agree per
  entry, coverage_tags are an enforced verbatim subset of §14, namespaces are defined, URLs
  confirmed. Two new tests prevent silent re-drift.
- `shape` registry/closed-enum is an **owned deferral**, not a gap: it is descriptive today; revisit
  only when a consumer routes on it.
- **Long-blog body (confirmed deferral, not changed):** per the brief, the synthetic chunking
  fixture stays a documented paper deferral for v0.3 — chunking is an *extract*-stage concern and the
  paid step-4 run is `--stage plan` (which skips this entry), so authoring a ~6k-word body now would
  build against a stage we are not running. Wire it when `--stage extract` goes live (Phase 3).
- **Provisional status — RESOLVED (2026-06-20, ADR 0104):** at the time of this ADR the walks still
  carried PROVISIONAL banners and the manifest noted "pending a human ground-truth pass." The
  architect has since confirmed the human pass complete; provisional status is now lifted across the
  walks, manifest, and CALIBRATION.md (ADR 0104). The calibration gate (the six CALIBRATION.md values)
  remains separate and still pending the paid `--stage plan` run.

## Docs touched (ADR 0084 — surfaced)

- `docs/eval.md §7.3` — added a "Coverage-tag namespaces" note (`target:`/`runtime:`/`platform:`
  definitions + the manifest-index-is-a-subset-of-the-walk-§14 discipline).
- `docs/schema.md §4.13` — one-line note that `platform:*` is an eval-coverage label, not a facet
  (so no one proposes `platform:*` facets).
