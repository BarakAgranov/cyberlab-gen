# 0003 — The deferral & integrity audit: cataloguing and judging the *aggregate* of Phase-1 lesser-paths

**Date:** 2026-06-09
**Status:** Report-only. **No code, no `docs/`, no ADR, no contract is changed by this
document.** It classifies and judges; it proposes nothing to implement. The maintainer reads
this and decides. (`recommended_action` per item is a triage *label* — fix-before-Phase-2 /
track-properly / accept-as-scope — and a statement of the intended end-state, not an
implementation instruction.)
**Scope:** every place the Phase-1 build took a *lesser path* — explicit ADR deferrals,
stopgaps, "superseded/amended" first-cuts, code-marker TODO/placeholder/reserved, and **silent**
code-vs-doc divergences never recorded as a decision at all. Each is sorted into one of four
categories and judged against one question: **does it compromise the AttackSpec contract Phase 2
will consume?** HEAD = `251d401`.
**Method:** an 8-reader fan-out over the whole corpus (ADRs 0001–0066 in four clusters; a
code-marker/silent sweep of `cyberlab_gen/` + `eval/runner/`; a doc-vs-code gap pass on the live
extract pipeline; a contract-itself pass on the AttackSpec schema / provenance / persistence; a
verifier of the execution log's own "deferred" lists + investigations 0001/0002), de-duplicated
to **46 candidates**, then each candidate **adversarially verified** (a second skeptic on every
category-3/4 and every contract-impacting item, charged with *refuting* the finding). 77 raw →
46 deduped → verified. The verification did real work: it caught **five "still-deferred" claims
that were already shipped or fixed** and demoted them (see §5).
**Provenance:** reconstructed this session from a read of the code, the ADRs, the docs, the
execution logs, and investigations 0001/0002 — **no run executed, no provider-backed eval run
(real money).** Each claim is tagged `[V]` (verified in code/ADR/doc I or a verifier opened this
session) or `[I]` (inferred). The single must-fix finding and both silent (cat-4) findings, the
pickle carry-forward, and the boundary-correcting demotions were **re-verified by hand** against
the cited files before being written here.

## Why this exists

Across this build many decisions deferred work. Most were judged locally and correctly. The risk
this audit exists to surface is the **aggregate**: nobody had assembled the full list and asked
whether the *sum* of "not a blocker, defer it" calls had quietly drifted the system off its
intended design — and, specifically, whether any deferral leaves **Phase 2 building on something
unsound**. A stopgap with a working workaround and no scheduled replacement becomes permanent by
default; a silent code-vs-doc gap is worse, because no one chose it. The calibration example is
the persistence pickle reality (ADR 0040/0066): the entire persisted spec falls back to `pickle`
because it carries `HttpUrl`, so every nested type must stay picklable *forever* — a structural
constraint that outlived the acute crash it caused.

## The headline (read this first)

**Discipline mostly held. This is a reassuring result and it is not inflated.** Of ~39 distinct
verified lesser-paths, the overwhelming majority are **category 1** (legitimate Phase-2+ scope) or
**category 2** (tracked stopgap-with-expiry — and roughly a dozen of those are *already closed*:
the maintainer repeatedly caught real latent bugs and fixed them at the root with regression
tests rather than deferring them — the opposite of "avoid touching too much"). The category-3
cluster is small and is almost entirely a **tracking-granularity** problem, not dangerous
correctness workarounds; **none of the cat-3 items compromise the contract.**

| Category | Count | Compromises the contract? |
|---|---|---|
| 1 — Legitimate scope | 16 | none |
| 2 — Tracked stopgap-with-expiry (≈12 already RESOLVED) | 14 | none (resolved ones strengthened it) |
| 3 — Dangerous / tracked-too-thin | 7 | none today (1 is the latent carry-forward) |
| 4 — Silent (code-vs-doc) | 2 | **1 yes**, 1 partly |

**There is exactly ONE must-fix-before-Phase-2 contract item:** the Extractor-Jury's 0.7 rubric
floor is **prompt-only** — the framework ships on the jury's verdict *enum* and never checks
`verdict.scores` against the floor, the helper methods that would do it are **dead code**, and —
worst — **ADR 0021 pt 5 falsely asserts a `model_validator` already enforces it.** Everything else
that touches the contract was either already resolved before this audit or is contract-neutral.

**The Phase-1/Phase-2 boundary is drawn honestly** (§2): nothing was pushed across the line merely
to call Phase 1 done.

---

## 1. The classified catalogue

Grouped by final (post-verification) category. `[V]`/`[I]` and `file:line`/ADR throughout. The
contract-touching and silent items get full treatment; the large legitimate-scope and
already-resolved sets are tabulated compactly.

### 1.1 Category 4 — SILENT (found by reading code vs docs, never recorded as a decision)

These are the worst kind because no one chose them. There are exactly two, and only the first
compromises the contract.

#### ★ 4-A `jury-rubric-floor-not-mechanically-enforced` — **the single must-fix.** `[V, re-verified by hand]`

- **Current state.** `agents.md §5.5:142` makes the floor a **mechanical pass-RULE**: *"An
  AttackSpec passes if all dimensions score above their floor. Default 0.7."* In code the floor is
  injected into the **LLM prompt only** (`jury.py:137`); `rubric_floor` is otherwise unused
  (`jury.py:81-83`). `jury_node` (`orchestrator.py:683-695`) routes **solely on
  `verdict.verdict`** — `APPROVE → SHIPPED` — and **never reads `verdict.scores`**.
  `JuryVerdict._verdict_consistency` (`schema.py:93-104`) checks **only feedback-item counts**,
  never `self.scores`. `JuryScores.all_above` / `min_dimension` (`schema.py:49-60`) are **dead
  code** — a source-wide grep finds zero call sites — proving the wiring was intended and never
  connected. And **ADR 0021 pt 5** (`dev/decisions/0021-…md:28`) affirmatively states the
  verdict↔score rule (incl. *"a dimension floor breach consistent with >30% mismatched"*) *"is
  enforced by a `model_validator`"* — the validator does the opposite.
- **Why it's here / real end-state.** Never consciously deferred — a silent doc/ADR-vs-code
  divergence. End-state: the **framework** derives or vetoes the verdict by reading
  `verdict.scores` against `rubric_floor` (defense-in-depth so a self-contradictory
  *approve-with-low-scores* cannot ship), and ADR 0021 pt 5 is corrected to stop vouching for a
  gate that isn't wired.
- **Compromises the contract? YES.** The jury verdict is one of the three guarantees that vouch
  for the shipped AttackSpec. A Phase-2 reader who trusts a "jury-approved" stamp inherits a
  quality gate that **both** the architecture doc **and** the ADR claim exists but that is **absent
  from the decision path**: a verdict of `approve` with every dimension at 0.1 ships.
  - *Framing, precisely (the skeptic's steelman, adjudicated):* routing on the **jury's** verdict
    is itself the `architecture.md §1.5`-sanctioned pattern — the jury is the *reviewer*, and
    `orchestrator.py:685` correctly cites §1.5 for "the jury owns the ship decision." So this is
    **not** a clean §1.5 violation, and 0.7's *value* is admittedly an `§8.4` placeholder. The
    defect is narrower and survives that steelman: the **mechanical score-floor cross-check** the
    docs+ADR promise does not exist, the dead helpers show it was meant to, and ADR 0021 **falsely
    vouches** that it does — which makes a Phase-2 reader's trust *worse* than a merely silent gap.
    The producer/reviewer's own dimension scores are simply ignored at the ship gate.
- **Recommended action:** **fix-before-Phase-2** + correct ADR 0021 pt 5.
- **Evidence:** `jury.py:137,81-83`; `schema.py:49-60,93-104`; `orchestrator.py:683-695`;
  `agents.md §5.5:141-142,144`; `dev/decisions/0021-…md:28`.

#### 4-B `completeness-floor-not-enforced-and-llm-authored` — silent, **partly** compromising. `[V, re-verified]`

- **Current state.** `completeness_score` (`attack_spec.py:410`) is a **bare LLM-authored float**
  on the LLM-authored `ExtractionMetadataBlock` — a source-wide grep finds it **only at its
  declaration** (zero framework writer, zero consumer). `agents.md §5.4:97` defines it
  *mechanically* — *"the fraction of content fields populated with non-`unknown_from_blog`
  provenance. Default floor: 0.5"* — and no 0.5 ship-gate exists in
  orchestrator/static/grounding/`extract.py`. The eval harness recomputes its own
  `structural_completeness` separately (`eval/runner/metrics.py`).
- **Compromises the contract? PARTLY.** The missing 0.5 gate does **not** strongly compromise it:
  the Extractor-Jury's *own* completeness rubric dimension (gated at the stricter 0.7 default)
  catches substantive incompleteness, so shipping is not unguarded. And — unlike the ADR-0065
  defect — there is **no present live consumer** reading the field as a framework fact. What
  remains is a **silent LLM-authored-field-presented-as-a-framework-fact smell** on a persisted
  contract field (same *family* as the ADR-0065 `proposed_by_model`/`lineage.model` defect, but
  unfixed for this one) that a future Phase-2/eval consumer could read as truth.
- **Recommended action:** **track-properly** (compute it framework-side and gate/flag it, or stop
  presenting an LLM self-report as a framework fact; the jury covers the substantive gate
  meanwhile).
- **Evidence:** `attack_spec.py:405-413`; `agents.md §5.4:97`; `eval/runner/metrics.py:44-70`;
  `dev/decisions/0065-…md`.

*(A third, benign silent gap — `requires-user-confirmation-flag-unset-phase1` — was found and is
listed in §1.2 because its contract verdict is "no"; it is cat-4 by signature.)*

### 1.2 Category 3 — DANGEROUS / tracked-too-thin (real, contract-safe, but tracking can rot)

The honest finding here: this cluster is **not** a set of dangerous correctness workarounds. It is
a set of **genuine, contract-safe deferrals whose tracking is a single sentence / a
flag-for-maintainer note / a milestone that has already shipped without the obligation being
picked up** — i.e. the canonical "tracked-for-something-else, therefore really untracked" pattern.
None compromise the contract. The first is the genuine **latent carry-forward**; the rest are
low-severity.

| # | Item (file:line / ADR) | Why it's cat-3 (tracking) | Contract impact | Action |
|---|---|---|---|---|
| 3-A | **`persisted-spec-pickle-fallback-no-durable-guard`** — `checkpointing.py:42,48-82` sets `pickle_fallback=True`; the `HttpUrl`-bearing AttackSpec subtree *intentionally* rides pickle, absent from the msgpack allowlist `[V, re-verified]` | the durable fix (msgpack-encode `HttpUrl`, drop `pickle_fallback`) is tracked by **one execution-log sentence** (`phase-1-execution-log.md:2410-2414`), **absent** from the consolidated honest-deferred list, **no owning ADR**; deferred partly because it "reopens ADR 0040 territory" | **no, today** — every current nested type is picklable (`HttpUrl`, enums, the ADR-0066-fixed `Provenance` family) and round-trip + mid-abort recovery tests are green. **The genuine latent carry-forward:** the just-fixed `Provenance[Severity]` crash *proves the class is reachable*; a Phase-2 manifest/IaC type added to the persisted surface would inherit `pickle_fallback` with **no mechanical picklability guard**. | track-properly |
| 3-B | `no-input-side-chunking-for-long-blogs` — `pipeline.md:88` / `agents.md:109` mandate input chunk-and-reconcile; `ingestion.py` caches full text with no length/chunk logic; `extractor.extract()` is single-pass `[V]` | the only nearby tracking is for the **orthogonal OUTPUT-emit** class-fix (ADR 0032/0033); the input mandate has no ADR | **no** — oversized input HALTS (`status: failed`) and ships nothing; a coverage/availability gap, not correctness | track-properly |
| 3-C | `run-report-internal-traces-half-deferred` — `output.py:1-19`: the `coding-conventions.md §6.3` "internal traces always written to the structured report" duty is deferred; only Phoenix spans wired `[V]` | deferred in ADR 0013 by **one sentence**, pinned to "the run-report runner" — which **has now shipped** without the duty being picked up or ADR-closed | **no** — outcomes/cost/artifacts persist on every exit path (ADR 0039/0053); only the human-facing trace *section* is missing | track-properly |
| 3-D | `schema-details-basemodel-to-artifactmodel-doc-sweep` — ~40 `BaseModel(extra="forbid")` classes in `schema-details.md` not swept to `ArtifactModel` `[V]` | "tracking" is opportunistic per-section correction + flag-for-maintainer notes ("not part of this change"), **no sequenced sweep task** | **no** — the **code already uses `ArtifactModel`** (ADR 0004, verified); only docs-vs-code drift persists | track-properly |
| 3-E | `material-discrepancy-populated-doc-mirror-pending` — `attack_spec.py:464` field is **framework-populated** by enrichment (`enrichment.py:640-648`, sole writer) `[V]`; `schema-details.md §4` lacks the block | behavioral population is fully built; only the doc mirror is deferred, by a flag-for-maintainer note | **no** — framework-only authorship preserved (§1.5/§4.9); a summary index, not a competing authority | track-properly |
| 3-F | `run-id-not-threaded-into-registry-loader-raises` — all 6 `RegistryLoadError` raise sites omit `run_id` (`loader.py:129,138,167,209,217,241`) `[V]` | a single ADR-0009 Consequences sentence pointing at "the Phase-1 pipeline-runner task", no scoped owner | **no** — registry-load aborts *before* the Extractor and the error's `run_id` is never read back; diagnostic ergonomics only | track-properly |
| 3-G | `anthropic-live-cassette-pending` — ADR 0027's "real API call succeeds" exit criterion is **PENDING**; the live test skips, the cassette dir does not exist, ADR 0036 deleted the stale one `[V]` | the proposed "replacement" (ADRs 0028–0034) is **unrelated eval-loop hardening**, not a cassette-recording work item; tracked by one ADR-0027 sentence + the skip message | **no** — regression-confidence only; adapter behavior is fully unit-tested offline against a fake | track-properly |

### 1.3 Category 2 — TRACKED stopgap-with-expiry

Split into **(a) still-open, genuinely tracked** and **(b) already RESOLVED** (the audit's most
reassuring finding — the maintainer fixed real latent issues at the root rather than carrying
them).

**(a) Open, tracked to a real scoped item:**

| Item | Stopgap now | Replacement actually captured & sequenced? | Contract |
|---|---|---|---|
| `loop-budget-not-threaded-into-orchestrator` — three stopgaps (E1 over-cap report `extract.py:856-869`; B2 post-Extractor estimate `:625-632`; B2 `--auto` hard-stop) `[V]` | cost-aware checks fire only at the post-Extractor boundary, not in-loop | **Yes — ADR 0063** (PLANNED) is a genuine numbered work-stream scoping all three + the `build_pipeline` signature change + a gate. *(Nit: `extract.py:866` mis-cites "ADR 0065"; the work-stream is 0063 — a one-line comment fix.)* | **no** — governs cost/spend/proposal timing; the mechanical $25 ceiling (ADR 0047) is untouched |
| `cve-hallucination-check-inert-in-ship-path` — no `NvdClient` wired in production (`main.py:265-267`, `orchestrator.py:465`); `_check_cves` returns `[]` (`grounding_validator.py:242-243`) `[V]` | the `CVE_HALLUCINATION` mechanical check the validation contract lists is a no-op in production | Partly — at **investigation-doc/ADR granularity** (investigation 0001 §5, ADR 0055/0058): concretely scoped (named adapters, file:line) but **not** a numbered work-stream ADR like 0063 | **partly** — provenance is **never falsified** (an unconfirmed CVE keeps blog/`llm_inference` provenance, never a false `external_api` stamp; jury reviews vs the blog), but the advertised mechanical ship-gate is inert; **promote tracking to a work-stream ADR** |
| `mitre-local-seed-not-adapter` — 8-entry bundled seed, not a live adapter (`loader.py:185-198`); uncatalogued well-formed ids pass through as **unverified** `[V]` | verification-coverage via a seed, not an adapter | Yes — ADR 0058 Deferred + investigation 0001 §5 (operation-level, not a dedicated ADR) | **no** — `MitreTechniqueId` carries **no Provenance envelope** (`primitives.py:92`), so the seed cannot stamp false provenance; ADR 0058 made `mitre_hallucination` unproducible for well-formed ids |
| `advisory-source-typed-as-tool-id-and-sor-unverified` — `AdvisoryReference.source` is `ExternalDataSourceId` (a `SnakeName` alias) holding a *publisher label* like `aws` (`attack_spec.py:273`); `cve.source_of_record` post-enrichment verification deferred `[V]` | the misfiring pre-enrichment check was removed (ADR 0058); the retype + post-enrichment check wait on the adapter build | Partly — Deferred-list bullets + investigation rows; **thin for the `source_of_record` sub-piece** (no owning ADR) | **partly** — sound today (ships a valid `SnakeName`; `source_of_record` is framework-authored and Phase-1-inert), but a latent **mistyped-field/reserved-but-unenforced** trap a naive Phase-2 consumer could misread |

**(b) RESOLVED before this audit — discipline visibly held (the maintainer fixed, didn't defer):**

| Resolved item | How it was closed (ADR / commit) `[V]` |
|---|---|
| `provenance-custom-enum-pickle-bug-resolved` — the primer's named "Provenance pickle fragility" | `Provenance.__reduce__` + `_rebuild_provenance` (`provenance.py:148-184`); regression tests; **ADR 0066 / commit `251d401`** |
| `proposed-by-model-llm-self-report-fixed` — a real §1.5 false-provenance violation | framework-stamps the billed model (`extract.py:799-820`); **ADR 0065 / commit `514ba03`** |
| `overlay-write-at-acceptance-time-orphan-write` — cross-run registry pollution | moved to **spec-ship time**; **ADR 0050 → 0062**; `ProposalCapExceeded` raise gone |
| `overlay-key-type-bug-fixed` — facet proposals structurally impossible (`SnakeName`-keyed) | retyped to `RegistryKey` (`primitives.py:106`, `registries.py:371-378`); 3 non-vacuous tests; **ADR 0015** |
| `catastrophe-ceiling-failure-path-superseded` — ceiling not enforced on billed *failures* | enforced on every billed call incl. the failed path (`cost_recording_provider.py:201`); **ADR 0038 → 0047** + test |
| `proposal-tool-rejections-never-fatal-fixed` — optional-proposal `is_error` → fatal | rejection sites return `is_error=False` (`tools.py:333,362,374`); **ADR 0042 → 0043** |
| `grounding-routes-as-retry-not-patch` — ADR 0051's provisional "patch" wording | grounding routes as full RETRY (§1.7/§6.10.1); **ADR 0060** |
| `targeted-patch-refinement-code-landed` — ADR 0048 deferred the code | `RefinementPatch` + `apply_field_patch` (`framework/refinement.py`); **ADR 0054** + tests; D1/D2 obviated by inline provenance |
| `run-store-single-authority-checkpoint-read` — L4 lost-partial-spec seam | reads the checkpoint on every exit path (`extract.py:196-213`); **ADR 0053** + `test_checkpointing.py` |
| `cve-resolution-skip-when-no-nvd-honest-fallback` — honest "couldn't check" | `_nvd_lookup` records a not-found, never errors (`tools.py:277-286`) |
| `overlay-dir-phase0-stopgap-resolved` — `default_overlay_dir` Phase-0 stopgap | `LocalState` is the real owner; **ADR 0010 → 0012** |
| `conventions-py-cap-doc-bug` — doc said `<3.14` | already `>=3.13,<3.15` (`coding-conventions.md:14`, commit `307ca93`) — see §5 |

### 1.4 Category 1 — LEGITIMATE Phase-2+ scope (deferring is correct)

Confirmed genuinely out-of-scope, not Phase-1 corners relabelled. None compromise the contract.

| Item | Why legitimately deferred `[V]` |
|---|---|
| `external-data-source-adapters-deferred` (NVD/MITRE/OSV) | the primer's canonical cat-1; no adapter code exists, grounding/enrichment **degrade as honest skips**; ADR 0055/0058, investigation 0001 §5 |
| `output-emit-truncation-no-streaming-class-fix` | a truncated emit raises `EmitTruncated` and **ships nothing** (`call_surface.py:197-205`); streaming/sectioned emit is documented Phase-5 work; only the 20K `max_tokens` *value* is a stopgap *(verifier corrected cat-2 → cat-1: the fail-fast behavior is a complete durable design)* |
| `alternative-paths-captured-not-generated` | `chain.alternative_paths` is a fully-typed, provenance-bearing reserved block; v1 captures, v1.5+ generates (three architecture-layer docs say so); only consumer is the Phase-2 Docs Generator |
| `material-discrepancy-third-review-surface-phase4` | the interactive accept-API/accept-blog/abort prompt is doc-sanctioned Phase-4; report-only matches the documented `--auto` semantics |
| `resume-flag-deferred` | the checkpointer **capability** landed (ADR 0040) and is consumed by persistence; only the additive `--resume` CLI entrypoint is deferred |
| `runlineage-layer4-best-effort` | a run-index record (`run.json`), separate from AttackSpec `Provenance[T]`; gates nothing; the §1.5-overlapping `model` field is framework-sourced |
| `pending-proposals-value-types-execution-contexts-inert` | inert because **no AttackSpec field references either vocabulary**; `execution_contexts` is doc-scoped to the Phase-2 Planner (ADR 0044:39) *(verifier resolved a cat-4↔cat-1 split to cat-1 + a one-line guard note)* |
| `thesis-types-bundled-seeding-not-done` | intentional (ADR 0045): rely on the **shipped, verified** runtime-propose→overlay→promotion mechanism rather than pre-empt the telemetry signal |
| `escalate-persistent-transient-to-abort-deferred` | eval-runner-only control flow; honors the architecture's transient-retry contract; no billed tokens, no fabricated success |
| `stdlib-html-parser-over-bs4` | upstream text-quality tradeoff with an explicit eval revisit trigger; worse normalization at most lowers fidelity, which the same gates judge |
| `ingestion-failure-fixtures-mocktransport-not-vcr` | a **safety-forced** hermetic test technique (recording bot-walls edges toward probing anti-automation), **blessed in `coding-conventions.md §8.7`** |
| `archived-eval-reports-no-migration` | the `§0.6` no-migration call; stale `layer1_*` keys live only in 6 historical `eval/reports/*.yaml`; **zero** in `cyberlab_gen/` |
| `blogset-manifest-no-loader-phase4` | the loader **shipped in Phase 1** (ADR 0025, `eval/runner/manifest.py`); only coverage-tag *vocabulary validation* is genuine Phase-4 scope *(verifier corrected cat-2 → cat-1 — see §5)* |
| `blog-prefix-cachepoint-deferred` | within-run caching landed (ADR 0059); cross-extract reuse needs a locked-surface (`Message.content` → structured sequence) change; pure cost, no contract surface |
| `pricing-rankings-overlay-and-openai-placeholders` | user-overlay + a second adapter are out of Phase-1 scope; OpenAI rows are **guarded-inert** at three points (no silent mis-billing); Anthropic uses real rates |
| `registry-not-shipped-in-wheel` | no Phase-0/1 distribution story; the whole pipeline runs from source and Phase 2 builds on in-tree code; a future wheel task (+ regression test) is sequenced in ADR 0010 |
| `max-llm-cost-ignored-by-stub-verbs` | affects only stubbed downstream verbs (excluded from contract impact by definition); the live extract path consumes the flag and ledger |
| `platformdirs-hardcoded-root` | the hardcoded `~/.cyberlab-gen` root is architecture-mandated (§2.2/§2.3); `platformdirs` is **actively used** by `logging_setup.py` — *not* a dead dep — see §5 |

---

## 2. Verdict 1 — Is the Phase-1/Phase-2 boundary drawn honestly?

**Yes.** I checked specifically for Phase-1 corners relabelled as "Phase 2 scope" and found **none
of consequence.** The large legitimate-scope items (real NVD/MITRE/OSV adapters, streaming/
sectioned emit, input chunk-and-reconcile, the Phase-4 third-review surface, alternative-path
generation, the `--resume` entrypoint, the blog-prefix `CachePoint`, the OpenAI adapter) are all
genuine future *capability*, consciously recorded, and not work Phase 1 was ever contracted to
deliver. The maintainer keeps an explicit **"Genuinely deferred to Phase 1.5/2 (honest list)"**
block (`phase-1-execution-log.md:2331-2348`) that anchors most deferrals to an owning ADR.

The **one honest blemish is granularity, not direction**: several genuine, contract-safe deferrals
are tracked at investigation-doc-row / Deferred-list-bullet / single-execution-log-sentence level
rather than as numbered work-stream ADRs (the gold standard set by ADR 0063). That thinness is the
entire basis for the cat-3 cluster (§1.2) — those items are real and out-of-scope, but their
tracking is thin enough to **rot into permanent-by-default**. The fix is promotion to a scoped
line, not new engineering.

Crucially, the verification phase **sharpened rather than blurred** the boundary: it caught items
whose "still deferred" framing was **stale** and demoted them to cat-1/closed (§5). Nothing was
found to have been pushed across the line merely to call Phase 1 done.

## 3. Verdict 2 — What actually compromises the contract Phase 2 builds on

The real must-fix set is **small and dominated by one item.** Adjudicating each contract-touching
finding:

1. **`jury-rubric-floor-not-mechanically-enforced` — YES, the single fix-before-Phase-2 item.**
   The mechanical score-floor pass-rule that `agents.md §5.5` **and** ADR 0021 pt 5 both describe is
   absent from the decision path; shipping turns entirely on the jury LLM's verdict enum with no
   framework cross-check against the same verdict's `scores`; the dead `all_above`/`min_dimension`
   helpers prove the wiring was intended; and ADR 0021 pt 5 **falsely vouches** that a
   `model_validator` enforces it. This leaves a guarantee that vouches for the shipped AttackSpec
   weaker than both the doc and the ADR claim. **(§1.1 4-A, re-verified by hand this session.)**

2. **`completeness-floor-not-enforced-and-llm-authored` — PARTLY → track, not must-fix.** The
   jury's own 0.7-gated completeness dimension covers the substantive gate, and the field has
   **zero live consumers** today, so the false-provenance angle has no present reader. It remains a
   silent LLM-authored-as-framework-fact smell to close. **(§1.1 4-B.)**

3. **The latent carry-forward — `persisted-spec-pickle-fallback-no-durable-guard` — NOT a must-fix
   today.** It is **sound now**: every persisted-spec nested type is picklable and the round-trip /
   mid-abort tests are green. The root bug it descends from (the `Provenance[Severity]` crash) is
   **already fixed** (ADR 0066). The exposure only materializes when Phase 2 adds a new persisted
   type that inherits `pickle_fallback` with no mechanical picklability guard. **Track it with a
   real line (it currently has only a sentence); do not fire-drill it.** **(§1.2 3-A.)**

4. **Everything else that touches the contract is either RESOLVED or contract-neutral.** Resolved
   before the audit: the pickle crash (0066), `proposed_by_model`/`lineage.model` false provenance
   (0065), the overlay orphan-write (0062) and key-type bug (0015), the catastrophe-ceiling
   failure path (0047), grounding-as-retry (0060), targeted-patch refinement (0054), the run-store
   single-authority read (0053). Contract-neutral by construction: the CVE/MITRE inertness ships
   **honest provenance with no false `external_api` stamp**; the cost/budget stopgaps govern spend
   timing, not spec contents; the eval/packaging/doc-sweep items never touch a shipped spec.

**Net:** Phase 2 can build on the persisted AttackSpec **once 4-A is fixed.** The provenance,
persistence, and validation guarantees are otherwise sound as of HEAD `251d401`.

---

## 4. The prioritized split — must-fix-before-Phase-2 vs safely-carry-forward

**MUST-FIX BEFORE PHASE 2 (1 item):**

- **`jury-rubric-floor-not-mechanically-enforced`** — wire `verdict.scores` against `rubric_floor`
  into the framework ship decision, and correct ADR 0021 pt 5's false `model_validator` assertion.
  This is the only finding that leaves a guarantee vouching for the shipped AttackSpec weaker than
  both the doc and the ADR claim.

**CARRY FORWARD, BUT PROMOTE THE TRACKING (thin-tracking, contract-safe — turn each sentence into a
scoped line so it can't rot):**

- `persisted-spec-pickle-fallback-no-durable-guard` (the latent carry-forward — give it an owning
  line / a mechanical picklability guard before Phase 2 grows the persisted-type surface)
- `completeness-floor-not-enforced-and-llm-authored` (close the silent LLM-authored-as-fact smell)
- `cve-hallucination-check-inert-in-ship-path` and `advisory-source-typed-as-tool-id-and-sor-unverified`
  (give the inert mechanical check + the `source_of_record` sub-piece an owning work-stream ADR)
- `no-input-side-chunking-for-long-blogs`, `run-report-internal-traces-half-deferred`,
  `run-id-not-threaded-into-registry-loader-raises`, `schema-details-basemodel-to-artifactmodel-doc-sweep`,
  `material-discrepancy-populated-doc-mirror-pending`, `anthropic-live-cassette-pending`

**SAFELY CARRY FORWARD AS-IS (legitimate scope + already-resolved):** the entire §1.4 cat-1 set
and the §1.3(b) resolved set, plus the well-tracked open cat-2 items (`loop-budget` → ADR 0063;
`mitre-local-seed`).

---

## 5. What the adversarial verification corrected (the boundary-sharpening demotions)

The double-skeptic pass paid for itself by catching candidates whose "still-deferred" framing was
**stale** — already shipped or fixed — and demoting them. Recording these so a future reader does
not re-flag closed work:

- **`conventions-py-cap-doc-bug`** — premise (doc says `<3.14`) is false: `coding-conventions.md:14`
  already reads `>=3.13,<3.15`, fixed in commit `307ca93`. → cat-2 **closed.** `[V]`
- **`blogset-manifest-no-loader`** — the Pydantic loader is **not** deferred: `eval/runner/manifest.py:39-150`
  shipped it in Phase 1 under ADR 0025 with tests. Only coverage-tag vocab validation is Phase-4. → cat-1. `[V]`
- **`platformdirs-hardcoded-root`** — `platformdirs` is **not** a dead dep: `logging_setup.py:26,54`
  uses `user_log_dir()` (ADR 0037). No removal pending. → cat-1. `[V]`
- **`overlay-key-type-bug`** — already fixed to `RegistryKey` in **both** code and the
  `schema-details.md` mirror; the candidate's "only the doc mirror remains" premise is stale. → cat-1. `[V]`
- **`run-store-single-authority-checkpoint-read`** — the code (read checkpoint, remove the
  in-memory second path) **landed and is tested**, not an open stopgap. → cat-1. `[V]`

Two further verifier reclassifications tightened severity rather than scope:
`output-emit-truncation` (cat-2 → cat-1: the fail-fast design is complete; only streaming is
Phase-5) and `requires-user-confirmation-flag-unset-phase1` (cat-1 → cat-4: the per-field review
*surface* is genuinely Phase-4, but `schema.md §4.9:491` makes the framework **setting** the flag a
present Phase-1 duty; `provenance.py:65` defaults it `False`, **no code sets it**, and nothing in
`dev/` records the omission — a benign silent gap, contract verdict **no**).

---

## Closing read

The aggregate is healthy. The sum of local deferral decisions did **not** drift the system off its
design: the boundary is honest, the dangerous-category cluster is a tracking-granularity problem
rather than a correctness-workaround problem, and the maintainer's habit through Phase 1 was to
**catch latent bugs and fix them at the root with regression tests** (the pickle crash, two
false-provenance §1.5 violations, the overlay orphan-write and key-type bug, the catastrophe-ceiling
failure path) rather than defer them. The one thing Phase 2 should not inherit unfixed is the
**jury rubric floor that the docs and ADR 0021 claim is mechanically enforced but isn't** — a
silent gap made worse by an ADR that vouches for it. Fix that one; promote the thin-tracking
sentences to scoped lines (especially the pickle-fallback guard before the persisted-type surface
grows); carry the rest forward.

## Cross-references

- **ADR 0021** (pt 5 — the false `model_validator` assertion to correct) · **ADR 0040 / 0066** (the
  `pickle_fallback` reality + the resolved `Provenance` crash) · **ADR 0065** (the false-provenance
  family the completeness field still echoes) · **ADR 0063** (the loop-budget work-stream — the
  gold standard for how a stopgap should be tracked) · **ADR 0055 / 0058** (external sources as
  tool adapters; the CVE/MITRE inertness) · **ADR 0053** (run-store single authority).
- **Investigations 0001** (external sources / convergence) and **0002** (emit truncation /
  model-attribution) — the NOW/LATER splits this audit verified as honestly tracked.
- **`phase-1-execution-log.md:2331-2348`** — the maintainer's own "Genuinely deferred to Phase
  1.5/2" honest list, confirmed accurate and complete except for the thin-tracking granularity
  noted in §2.
