# Phase 1 — Agent task brief

**Purpose.** Decompose Phase 1 of cyberlab-gen ("Extractor + Jury") into agent-sized tasks. By the end of Phase 1, `cyberlab-gen extract <url>` reads a blog and writes a validated `attack-spec.yaml` to a working directory. No planning, no generation — just extraction. Each task is something a single coding agent can complete in one focused session, with clear inputs, outputs, and exit criteria. Tasks are sequenced; later tasks depend on earlier ones, but several can run in parallel (see the sequencing summary).

**Audience.** Coding agents operating in the cyberlab-gen repo, each having read this brief plus the listed required documents for that task.

**Authority gradient.** When this brief and an architecture document disagree, the architecture document wins. This brief is the *decomposition* of work the architecture has already specified; it is not a place to make new architectural decisions. If an agent finds a real ambiguity, it stops and records the question in `dev/decisions/` rather than guessing — same discipline as Phase 0.

**Scope of record.** The single source of truth for Phase 1 done-ness is `implementation-plan.md §4` (scope §4.1, build inventory §4.2, calibration §4.4, exit criteria §4.5). This brief decomposes that section; if §4 evolves, the brief follows it.

**Phase 1 is bigger than its name.** "Extractor + Jury" is shorthand. The full build inventory (`implementation-plan.md §4.2`) is eight components: Ingestion, the Extractor agent, the pre-Planner enrichment skeleton, the Extractor-Jury agent, a minimal refinement coordinator, Validator Layer 1, the post-Extractor interactive interrupt, and the Phase 1 eval-harness additions. Plus the AttackSpec inner blocks that Phase 0 left as stubs. Do not skip the enrichment/Validator/eval pieces because the headline says "Extractor + Jury."

**Execution log.** Every task ends by appending an entry to `dev/phase-1-execution-log.md` (template at the bottom). The log is how Phase 1's lessons feed Phase 2's brief — and, as Phase 0 proved, it is also where doc-vs-code drift gets surfaced for the next brief-writer. Keep entries terse.

**Corrected citations.** Phase 0's execution log surfaced several stale citations in the architecture docs. This brief uses the *corrected* targets inline so agents don't chase dead references. Where a correction applies, it is flagged with **[corrected]** and the ADR that owns the decision. Do not "fix" the brief back to the stale doc value — the ADR is authoritative until the architect updates the doc.

---

## Task 0 (prep): Architect doc edits + un-defer the Phase-0 smoke check

**Goal:** Close out the doc-vs-code drift the closed-catalog and `proposals` fixes (ADRs 0015, 0016) left behind, and un-defer the catalog smoke check. This is an **architect/maintainer task**, not implementation — it edits `docs/`, which implementation tasks may not touch.

**Required reading:** `dev/decisions/0015-proposals-registry-key-type.md`, `dev/decisions/0016-closed-catalog-models.md` (their "Doc-improvement note" sections spell out exactly what to change).

**Work:**

1. `schema-details.md §6.6` line ~1416: change `proposals: dict[SnakeName, ProposalAuditBlock]` to `dict[RegistryKey, ProposalAuditBlock]`, and update the `_entry_key` resolver doc-comment to reflect the `ENTRY_KEY_FIELD` approach now in the code.
2. `implementation-plan.md §3.4` check 4: drop the "once those get Pydantic models" deferral for the five closed catalogs — they exist now (ADR 0016).
3. Confirm the Phase-0 catalog smoke check is now live (a pytest case per closed catalog: load `registry/<name>.yaml`, validate against its `catalogs.py` model). Phase 0's `test_registry_load.py` pattern is the template.

**Exit criteria:** The two doc edits are committed; `just verify` stays green; the closed-catalog smoke check runs and passes.

**Output notes:** Append a Task 0 entry to `dev/phase-1-execution-log.md`.

---

## Task 1: AttackSpec inner content blocks (fill the Phase-0 stubs)

**Goal:** Replace the Phase-0 `Any`/`dict` stubs in `AttackSpec` with the real inner content-block models. Phase 0 shipped the envelope (`spec_version`, `spec_kind`, `source`, `extraction_outcome`, `extras`) with inner blocks stubbed and `# TODO(phase-1)` comments naming the sections that fill them. This task discharges those TODOs.

**Required reading:**

- *Primary:* `schema-details.md §4` (the AttackSpec content blocks in full — chain, chain steps, thesis, real_world_incidents, defender techniques, defenses, reproducibility); `schema.md §4.8` (AttackSpec semantics).
- *Cross-references:* `schema.md §4.9` (Provenance — every content field is wrapped in `Provenance[T]`, already built in Phase 0); `schema.md §4.7` (where the closed enums `Severity`, `DetectionComponent`, `DetectionFormat`, `ThesisType` get consumed as fields — the enums exist from Phase 0, `thesis_types` is the open-set catalog from ADR 0016); the field-by-field cross-reference table in `schema-details.md §7` (maps every architectural field mention to its Pydantic model).
- *Convention:* `coding-conventions.md §4` (type discipline), `dev/decisions/0004-base-class-discipline.md` (every artifact model extends `ArtifactModel`).

**Inputs:** Phase 0 complete (envelope + Provenance + registries + catalogs all shipped and green).

**Work:**

1. In `cyberlab_gen/schemas/attack_spec.py`, replace each stubbed inner block with its real model per `schema-details.md §4`: `ChainBlock`/`ChainStep` (with `chain_step_excerpts`, per-step `reproducibility` tier, MITRE/CVE technique references), `ThesisBlock` (multi-value `types: list[ThesisType]`), `RealWorldIncidentsBlock` (tri-state `IncidentStatus`), defender-technique and defense blocks, the derived lab-level `ReproducibilityBlock`.
2. Add the top-level `gaps` list and the `material_discrepancies` list (per `implementation-plan.md §4.2` — the latter is populated by enrichment in Task 4; declare the field now).
3. Each content field is `Provenance[T]`, not bare `T` (per the §7 cross-reference table — e.g. `severity` is `Provenance[Severity]`).
4. Honor emergent lab class (`architecture.md §0.7`): the per-step `reproducibility` tier is authored by the Extractor and carried unchanged downstream; there is no upfront lab-class field. The lab-level `ReproducibilityBlock` is *derived*, not authored.
5. Remove the `# TODO(phase-1)` comments as each block is filled.
6. Extend `tests/unit/schemas/test_attack_spec.py`: each block constructs from its canonical shape, round-trips through YAML, and rejects unknown fields; a full representative AttackSpec (chain of 2–3 steps, thesis, provenance on every field) builds and round-trips.

**Exit criteria:**

- All AttackSpec schema tests pass; pyright strict clean for `cyberlab_gen/schemas/`.
- No `Any`/`dict` stubs or `# TODO(phase-1)` markers remain in `attack_spec.py`.
- A representative AttackSpec round-trips Python → YAML → Python to an equal instance.

**No discretion on:** Field names, types, validator logic, or the `Provenance[T]` wrapping — all from `schema-details.md §4` / §7. The emergent-lab-class discipline (no upfront lab class; per-step tiers authored, lab-level derived).

**Output notes:** Append a Task 1 entry to `dev/phase-1-execution-log.md`.

---

## Task 2: Provider call surface for agents (capability-hint dispatch)

**Goal:** Wire the Extractor and Jury (Tasks 3, 5) onto the Phase-0 provider layer so agent code requests a *capability hint*, never a model name. Phase 0 shipped the `Provider` interface, the cost ledger, and the ranking file; this task adds the agent-facing call path and the structured-output enforcement boundary.

**Required reading:**

- *Primary:* `pipeline.md §3.5` (provider abstraction — capability hints not model names, base-prompt-plus-overlay, structured-output enforcement at the boundary, multi-model jury support); `provider-interface.md` (the call surface, the ranking shape, the cost-tracking contract).
- *Cross-references:* `pipeline.md §3.7` (provider failure handling — retry-with-backoff, structural-malformed-response retries counted against retry budget); `architecture.md §1.5` (deterministic orchestrator vs. specialist LLMs — agents produce content/judgments, never route control flow).
- **[corrected]** When referencing the error hierarchy, `ProviderError` lives in **`cyberlab_gen/errors.py`** (top-level), not under `providers/` — per `dev/decisions/0009-phase0-error-hierarchy.md`. The Phase-0 `errors.py` already has three classes; add `ProviderError` (and later `ExtractionError`, `IngestionError`) to it as each is first raised. Do not create `providers/errors.py`.
- **[corrected]** `provider-interface.md §4.1` may show pre-PEP-695 generics (`Generic[T_Output]`) and `arbitrary_types_allowed=True`; the locked style is PEP-695 (`class Foo[T]`) without `arbitrary_types_allowed`. The `model_rankings.yaml` shape wraps entries under `by_capability`, not flat top-level (per Phase-0 log).

**Inputs:** Phase 0 complete; Task 1 complete (agents output `AttackSpec`).

**Work:**

1. Add the Phase 1 dependencies: `pydantic-ai`, `langgraph`, `httpx` — per `coding-conventions.md §10`'s **"Phase 1 (added when the first agent and orchestrator ship)"** list. **[corrected]** These are Phase 1 deps; do not add them in Phase 0, and `openai`/`anthropic` SDK clients are pulled in by their provider adapters here, not pre-staged.
2. Implement the agent call surface: a function/class that takes a capability hint (e.g. `long_context_extraction`, `high_quality_reasoning`, `fast_cheap_structured_output`) plus a typed output schema, resolves the highest-ranked *reachable* model via the ranking file, calls through the `Provider` interface, and parses the response against the output schema.
3. Structured-output enforcement at the boundary (`pipeline.md §3.5`): malformed responses are retried, counted against the retry budget (§3.7); after exhaustion, raise the agent-failure path. This is *structural* retry (Task 6's retry mechanism), distinct from refinement.
4. Base-prompt-plus-overlay loading: a prompt loader that reads a base prompt and optional model-specific overlay, so Tasks 3/5 can store prompts as files.
5. Tests: capability hint resolves to a reachable model and skips unreachable ones; a malformed structured response triggers a retry then an agent-failure on exhaustion; cost is tracked per model through the Phase-0 ledger.

**Exit criteria:**

- An agent can be invoked with a capability hint + output schema and returns a validated typed object (tested against a mock provider — reuse the Phase-0 `test_mock_provider` pattern).
- No agent-facing code references a concrete model name.
- Structural-retry path is exercised by a test; pyright strict clean.

**Decision discretion:** The exact signature of the call surface; whether prompts load eagerly or lazily.

**No discretion on:** Capability-hint-not-model-name (`pipeline.md §3.5`). The retry-vs-refinement split — structural malformation is retry, never refinement. `ProviderError` location (top-level `errors.py`).

**Output notes:** Append a Task 2 entry to `dev/phase-1-execution-log.md`.

---

## Task 3: Ingestion stage

**Goal:** `cyberlab-gen` can fetch a blog URL, normalize it, hash it, cache it, and record metadata — producing the `IngestionResult` the Extractor consumes. The `IngestionResult` model already exists from Phase 0.

**Required reading:**

- *Primary:* `pipeline.md §3.2.1` (Ingestion responsibilities and failure modes); `implementation-plan.md §4.2` "Ingestion stage" (the concrete checklist: 10s timeout, HTML→text preserving headings, SHA-256, cache path, metadata fields).
- *Cross-references:* `pipeline.md §3.7` (retry-with-backoff for transient fetch failures); `schema-details.md` for the `IngestionResult` field set (built in Phase 0).
- *Safety:* Ingestion never bypasses paywalls, bot detection, or CAPTCHAs — it fails with a clear message (`implementation-plan.md §4.2`, §4.6 risks).

**Inputs:** Phase 0 complete (`IngestionResult` exists). Independent of Tasks 1/2.

**Work:**

1. URL fetcher with a 10s default timeout (configurable) using `httpx`.
2. Content normalizer: HTML → text, preserving heading structure as markers the Extractor can use for narrative granularity.
3. SHA-256 hash of normalized text.
4. Cache writer to `~/.cyberlab-gen/cache/<blog-hash>/`; downstream stages read from cache and never re-fetch (protects against the blog changing mid-pipeline).
5. Metadata recorder populating `IngestionResult` (URL, canonical URL, fetched-at, fetch method, word count, publisher domain).
6. Failure modes with clear messages: URL unreachable; paywall (HTTP 403 / very-short body); bot-detected (Cloudflare interstitial). **Do not attempt to bypass any of these.** Add `IngestionError` to `cyberlab_gen/errors.py` **[corrected: top-level, per ADR 0009]**.
7. Tests: a cached fixture blog ingests to a correct `IngestionResult`; re-ingesting reads cache rather than re-fetching; paywall/bot/unreachable each raise `IngestionError` with a clear message. Use recorded HTTP fixtures (`pytest-recording`/VCR per `coding-conventions.md §10`) — record once, replay forever, cassettes checked in.

**Exit criteria:**

- Ingestion produces a valid `IngestionResult` for a real (recorded) blog; cache hit avoids re-fetch; the three failure modes are tested.
- Content-quality/scope judgment is **not** here — that's the Extractor's sole job (`pipeline.md §3.2.1`).

**Decision discretion:** HTML-to-text library; cache file layout within the blog-hash directory.

**No discretion on:** Never bypassing paywalls/bot-detection/CAPTCHA. Cache-then-read (no re-fetch). `IngestionError` location.

**Output notes:** Append a Task 3 entry to `dev/phase-1-execution-log.md`.

---

## Task 4: Pre-Planner enrichment skeleton + materiality check

**Goal:** A deterministic framework pass (never an agent) that walks `enrichment_triggers` from `external_data_sources` entries and enriches the AttackSpec with authoritative external data — Phase 1 scope is CVE (NVD) and MITRE technique references only. Implement the material-vs-non-material discrepancy classification now, even though Phase 1 doesn't surface material discrepancies at an interrupt yet.

**Required reading:**

- *Primary:* `pipeline.md §3.2.4` (pre-Planner enrichment — framework-only authorship, materiality-scaled surfacing, the discrepancy recording rule, external-API budget, rate-limiting); `implementation-plan.md §4.2` "Pre-Planner enrichment (skeleton)" and "External data sources (subset for Phase 1)".
- *Cross-references:* `schema.md §4.9` (the "framework-only-authorship" rule — only the framework, never an agent, sets `source: external_api` and the `discrepancy_with_blog: true` flag); the `external_data_sources` registry entry's `discrepancy_materiality_rules` field (shape built in Phase 0; this task reads it); `pipeline.md §3.7` (rate-limit/backoff).
- *Registry note:* NVD and MITRE entries exist in `registry/external_data_sources.yaml`; MSRC/OSV/KEV/EPSS/cloud-bulletins are stubs registered but not integrated — their absence is honest in `unknown_from_blog.reason` (`implementation-plan.md §4.2`).

**Inputs:** Task 1 (AttackSpec inner blocks, incl. `material_discrepancies` field). Task 3 helps for end-to-end testing but isn't strictly required for unit tests.

**Work:**

1. Framework code (not an agent) that walks `enrichment_triggers` and, for Phase 1, enriches CVE references via NVD and MITRE technique references.
2. The framework — never an agent — sets the enriched field's `source: external_api` with citations to both the blog passage and the API response.
3. Discrepancy handling: when the API contradicts a `blog_explicit` finding, rewrite the field with `source: external_api`, preserve both citations, and set `discrepancy_with_blog: true`.
4. Materiality classification per the entry's `discrepancy_materiality_rules`: non-material (same-tier CVSS, same CWE category, equivalent technique) → silent rewrite, recorded in provenance; material (cross-tier CVSS, different attack vector/CWE, contradicting technique) → populate the top-level `material_discrepancies` list. **Phase 1 lists material discrepancies in the run report only**; the third review surface comes in Phase 4 (`implementation-plan.md §4.2`).
5. External-API budget (default 100, configurable) with priority order CVEs > MITRE > GitHub > bulletins > other; skipped lookups get `unknown_from_blog` reasons naming what was skipped. Rate-limit handling records `unknown_from_blog.reason: "external API rate-limited at enrichment time"` and continues.
6. Tests: a CVE field gets NVD-enriched with both citations and `source: external_api`; a contradicting CVSS produces a `material_discrepancies` entry; a same-tier difference rewrites silently with the discrepancy recorded; budget exhaustion and rate-limiting both degrade gracefully. Use recorded NVD/MITRE fixtures (VCR).

**Exit criteria:**

- The materiality-check code path produces material/non-material records (this is an explicit Phase 1 exit criterion, `implementation-plan.md §4.5`).
- Framework-only authorship holds: no agent sets `external_api` provenance.
- Enrichment degrades gracefully on budget/rate-limit.

**No discretion on:** Framework-only authorship of `external_api` fields. Always recording discrepancies; the material/non-material split. Not surfacing material discrepancies at an interrupt in Phase 1 (report only).

**Output notes:** Append a Task 4 entry to `dev/phase-1-execution-log.md`.

---

## Task 5: Extractor agent + Extractor-Jury agent

**Goal:** The two agents. The Extractor reads cached blog content and produces an `AttackSpec`; the Jury reviews it and emits a `JuryVerdict`. This is the heart of Phase 1.

**Required reading:**

- *Primary:* `agents.md §5.4` (Extractor — job, inputs, tools, provenance discipline, the typed-value decision tree, quality bar, failure modes); `agents.md §5.5` (Extractor-Jury — verdict semantics, provenance verification, asymmetric calibration, disagreement handling); `pipeline.md §3.2.2` and §3.2.3 (the same two stages from the pipeline's perspective, incl. MITRE/CVE hallucination rejection, the `gaps` list, the researcher-stage seam).
- *Cross-references:* `schema.md §4.20` (provenance is categorical — the source is what produced the value, not a preference); `schema.md §4.15` (search-before-claim); `schema.md §4.10` (the typed-value decision tree: existing type vs. propose-new vs. `extras`); `schema.md §4.16` (proposal lifecycle — the Extractor proposes `value_types`, `target:*` and blog-derived `lab_class_signal:*` facets; `runtime:*` and lab-derived facets are the Planner's, not Phase 1's).
- *Provider:* Task 2's call surface (capability hint + output schema). Prompts live at `cyberlab_gen/agents/extractor/prompt.md` and `cyberlab_gen/agents/extractor_jury/prompt.md`, iterated first in `dev/prompt-iterations/` (`coding-conventions.md §10` / `implementation-plan.md §4.2`).
- *Calibration:* `eval.md §7.5` (the asymmetric-calibration mechanism); `architecture.md §8.4` (the 0.7 / 0.5 / N=2 defaults are placeholders pending eval data).

**Inputs:** Task 1 (AttackSpec), Task 2 (provider call surface), Task 3 (Ingestion provides blog content), Task 4 (enrichment runs between Extractor and the field the Jury verifies — though the Extractor→Jury wiring is Task 6).

**Work:**

1. **Extractor:** a Pydantic AI agent with `AttackSpec` as output type, invoked via Task 2's capability-hint surface (`long_context_extraction`). Base prompt at `cyberlab_gen/agents/extractor/prompt.md`.
2. Extractor tools: `external_lookup(source_id, params)` against `external_data_sources`; `propose_value_type`; `propose_facet` (only `target:*` and blog-derived `lab_class_signal:*`). The Extractor is read-only — no filesystem, no code execution, no URL fetching outside the tool interface.
3. Search-before-claim enforced at the framework level: agent traces are checked for tool-call evidence corresponding to every `source: external_api` field; an `external_api` field with no matching tool call is a rejection (counts against retry budget).
4. MITRE/CVE hallucination check: technique IDs validated against the bundled MITRE reference, CVEs against NVD; a hallucinated ID rejects the output and re-prompts with the specific ID flagged (retry-budget, not refinement).
5. Provenance discipline (categorical): `blog_explicit` when the blog states it; `llm_inference` when the field needs filling and the blog implies (marked + cited, never silently `blog_explicit`); `unknown_from_blog` otherwise. Populate the top-level `gaps` list and `chain_step_excerpts`. Use the researcher-stage seam convention (`unknown_from_blog` with `reason: "requires external research"`) where external lookup would help but isn't wired.
6. Scope decision: set top-level `extraction_outcome` (`in_scope` | `out_of_scope`) with a reason; out-of-scope triggers the §3.1.1 notice (halts in `--auto`).
7. **Extractor-Jury:** a Pydantic AI agent with a `JuryVerdict` output schema `{verdict, scores, feedback, retry_recommended}`. Prompt at `cyberlab_gen/agents/extractor_jury/prompt.md`. Same tool inventory as the Extractor (for independent `external_api` verification). Verdict semantics: `approve` → continue; `revise` (1–3 fields with citation problems) → field-targeted re-run; `reject` (>30% of content fields mismatched, i.e. systematic hallucination) → halt. Rubric dimensions (fidelity, completeness, provenance correctness, structural validity), each 0–1, default floor 0.7.
8. Record the asymmetric-calibration discipline in `CALIBRATION.md`: tune *up* on false-approval, never *down* on false-rejection (`agents.md §5.5`, `eval.md §7.5`).
9. Tests (against a mock/recorded provider): Extractor produces a schema-valid AttackSpec from a fixture blog with provenance on every field; an `external_api` field with no tool-call trace is rejected; a hallucinated MITRE ID is rejected and re-prompted; out-of-scope content sets `extraction_outcome`. Jury: `approve`/`revise`/`reject` each fire on constructed AttackSpecs; provenance-mismatch detection works for each source kind.

**Exit criteria:**

- Extractor produces schema-valid AttackSpecs with categorical provenance; search-before-claim and MITRE/CVE hallucination checks both enforced.
- Jury emits well-formed `JuryVerdict`s with the three verdicts and provenance verification.
- Prompts are versioned files; asymmetric-calibration discipline recorded in `CALIBRATION.md`.

**No discretion on:** Categorical provenance (`schema.md §4.20`); search-before-claim (§4.15); the Extractor being the only value-type proposer and *not* proposing `runtime:*` facets; verdict semantics and thresholds; asymmetric calibration.

**Output notes:** Append a Task 5 entry to `dev/phase-1-execution-log.md`. This task will generate the richest doc-improvement notes — record prompt-design friction and any place the agent specs were ambiguous.

---

## Task 6: Validator Layer 1 + minimal refinement coordinator + orchestration

**Goal:** Wire the stages into the deterministic state machine, with Validator Layer 1 gating structure and a minimal refinement coordinator handling the Extractor→Jury cycle. This is where the retry-vs-refinement split gets implemented correctly — even though Phase 1 has only one agent, getting it right now matters (`implementation-plan.md §4.2`).

**Required reading:**

- *Primary:* `validation.md §6.4` (Layer 1 — JSON Schema validation, registry reference resolution, `spec_kind` discriminator); `validation.md §6.10` (failure routing — Layer 1 failures go to the responsible agent's *retry* mechanism, NOT refinement); `pipeline.md §3.1` (the deterministic state machine, `--auto`/`--interactive` modes, headless rejection of `--interactive`); `implementation-plan.md §4.2` "Refinement coordinator (minimal v1)" and "Validator Layer 1 (skeleton)".
- *Cross-references:* `architecture.md §1.5` (deterministic orchestrator, specialist LLMs — the orchestrator routes, agents never do); `architecture.md §1.7` (retry vs. refinement — the distinction this task must encode); `pipeline.md §3.3` (cross-stage contracts — the Ingestion→Extractor→Jury→enrichment typed boundaries); `dev/decisions/` for the validator-location decision (record it when Layer 1 first ships — likely `cyberlab_gen/validators/layer1.py` per `coding-conventions.md`).
- *Orchestrator:* LangGraph (`architecture.md §1.5`); Phase 0 §3.7 risk note advised sketching this pipeline on paper before committing — do that first if not already done.

**Inputs:** Tasks 1, 3, 4, 5 (the stages to wire). Task 2 (provider surface) underlies the agents.

**Work:**

1. Validator Layer 1: JSON Schema validation over the AttackSpec; registry reference resolution (every type/facet referenced exists in the merged registry — including the closed catalogs from ADR 0016); `spec_kind` discriminator enforcement. Add `ExtractionError`/`ValidationError` to `cyberlab_gen/errors.py` **[corrected: top-level, per ADR 0009]** as needed.
2. Failure routing (the critical discipline): Layer 1 structural failures route to the agent's **retry** mechanism (Task 2's structural retry), *not* the refinement coordinator. Encode this split explicitly (`validation.md §6.10`).
3. Minimal refinement coordinator: after a Jury `revise` verdict, re-run the Extractor with structured feedback wrapped in a `UserFeedback`-like object; count per-agent iterations against a per-agent cap (placeholder 3, revisit in Phase 4); on cap exhaustion, ship the last AttackSpec with `low_jury_confidence: true` and the unresolved feedback in the run report.
4. Orchestration: assemble Ingestion → Extractor → Validator-Layer-1 → Extractor-Jury → enrichment as a LangGraph state machine with the typed cross-stage contracts from `pipeline.md §3.3`. The orchestrator routes; agents only produce content/judgments (`architecture.md §1.5`).
5. Disagreement-without-progress handling per `pipeline.md §3.2.3`: retry exhaustion distinguishes `reject` (halt) from `revise` (proceed with `low_jury_confidence`).
6. Tests: a Layer-1-invalid AttackSpec routes to retry, not refinement (assert the path); a Jury `revise` triggers a bounded re-run that stops at the cap and ships with `low_jury_confidence`; a `reject` halts; the full Ingestion→…→enrichment graph runs end-to-end on a fixture blog producing an enriched, validated AttackSpec.

**Exit criteria:**

- The retry-vs-refinement split is implemented and tested (Layer 1 → retry; quality → refinement).
- The state machine runs end-to-end on a fixture blog.
- Cap-exhaustion ships with `low_jury_confidence`; `reject` halts.

**No discretion on:** Layer 1 failures → retry, never refinement (`validation.md §6.10`). The orchestrator routes; agents don't (`architecture.md §1.5`). The deterministic-state-machine / typed-boundary discipline.

**Output notes:** Append a Task 6 entry to `dev/phase-1-execution-log.md`.

---

## Task 7: `extract` CLI verb + post-Extractor interactive interrupt

**Goal:** `cyberlab-gen extract <url>` runs the Task 6 pipeline and writes `attack-spec.yaml` to a working directory, with the `--interactive` (default) and `--auto` modes and the four-option post-Extractor interrupt. Phase 0 shipped the CLI verbs as stubs; this fills `extract`.

**Required reading:**

- *Primary:* `pipeline.md §3.2.5` (post-Extractor interrupt — the three review surfaces, the four-option menu, the per-proposal Accept/Edit menu, `$EDITOR` revalidation); `pipeline.md §3.1` / §3.1.1 (modes, headless rejection, inline notices vs. typed-artifact interrupts, budget-overrun interrupts in both modes); `implementation-plan.md §4.2` "Post-Extractor interrupt".
- *Cross-references:* `dev/decisions/0001-click-vs-typer.md` (the CLI is `typer`); `dev/decisions/0013-cli-flag-surface.md` (the flag surface locked in Phase 0); the Phase-0 CLI stubs and `test_cli.py` pattern.
- *Phase 1 caveat:* the third review surface (material discrepancies) is **not** in Phase 1 — material discrepancies are listed in the run report only; the surface comes in Phase 4 (`implementation-plan.md §4.2`, §4.5).

**Inputs:** Task 6 (the pipeline to invoke). Task 1 (AttackSpec to serialize/edit).

**Work:**

1. Fill the `extract` verb (typer) to run the Task 6 pipeline on a URL and write `attack-spec.yaml` to the working directory.
2. `--interactive` (default): after Jury approval + enrichment, pause and show the AttackSpec with the four-option menu — Approve / Natural-language feedback (Extractor re-runs) / Edit in `$EDITOR` / Abort. Per-proposal menu for proposed value types/facets: Accept or Edit; edited proposals are structurally revalidated, and structurally-invalid edits reopen the editor with errors as comments.
3. `--auto`: skip all interrupts; auto-accept proposals up to the per-run cap (placeholder 5). Out-of-scope notice halts in `--auto` (§3.1.1).
4. Headless guard: when stdin is not a TTY, reject `--interactive` at startup with a message pointing to `--auto` (`pipeline.md §3.1`).
5. Budget-overrun interrupt in **both** modes: if estimated next-stage spend exceeds the configured cap, pause to raise cap / abort / proceed (the one exception to "`--auto` has no interrupts").
6. Material discrepancies (from Task 4): list in the run report only; no interrupt in Phase 1.
7. Tests (extend `test_cli.py`): `extract <url>` on a fixture writes a valid `attack-spec.yaml`; the four-option menu functions (simulate each choice); the per-proposal Accept/Edit menu functions and revalidates edits; headless `--interactive` is rejected; `--auto` runs without interrupts; budget-overrun pauses in both modes.

**Exit criteria:**

- `cyberlab-gen extract <url>` writes a valid AttackSpec; both modes work; the four-option and per-proposal menus function; proposal edits are revalidated (all explicit `implementation-plan.md §4.5` criteria).
- Headless rejection and budget-overrun-in-both-modes both work.

**No discretion on:** The four-option menu and per-proposal Accept/Edit semantics; headless rejection of `--interactive`; budget caps honored in both modes; no third review surface in Phase 1.

**Output notes:** Append a Task 7 entry to `dev/phase-1-execution-log.md`.

---

## Task 8: Eval harness Phase 1 additions

**Goal:** A per-blog eval runner that invokes the Extractor pipeline N times and records the Phase 1 metrics, plus the manual jury-decision review tooling that produces false-approval / false-rejection rates. Eval is built alongside, never after (`implementation-plan.md §1.2`).

**Required reading:**

- *Primary:* `eval.md §7.3` (the Phase 1 eval-harness additions); `eval.md §7.4` (the metrics available in Phase 1); `eval.md §7.5` (the calibration mechanism — false-approval/false-rejection rates and the asymmetric discipline); `implementation-plan.md §4.2` "Eval harness Phase 1 additions" and §4.4 (calibration items locked in Phase 1).
- *Cross-references:* `dev/decisions/0014-blog-set-manifest-schema.md` (the eval blog-set manifest schema — this lives in the ADR, **[corrected]** not yet promoted into `eval.md §7.3`; use the ADR as the authoritative shape); the Phase-0 eval blog-set scaffold and the curated blog walks in `dev/curated-blog-walks/`.
- *Harness invocation:* `just eval`, not pytest (`coding-conventions.md §10`); a small smoke test under `tests/eval/` verifies the harness starts.

**Inputs:** Task 7 (a runnable `extract`). The curated blogs and walks from Phase 0 (Task 8 of that phase).

**Work:**

1. Per-blog eval runner: invoke the Extractor pipeline N times (N=3 per exit criteria) per curated blog and record metrics — Layer 1 pass rate, cost per AttackSpec, structural completeness score, registry proposals issued, `extras` entries count (`eval.md §7.4`).
2. Manual jury-decision review tooling: a maintainer reads each AttackSpec and marks each jury verdict correct / false-approval / false-rejection; the tool aggregates per-blog false-approval and false-rejection rates (`eval.md §7.5`).
3. Reports archive to `eval/reports/` (exit criterion).
4. Grow the curated set to 3–5 blogs if Phase 1 surfaces blog shapes the Phase-0 three don't cover (`implementation-plan.md §4.3`) — include at least one long blog to exercise chunking (§4.6 risk).
5. Record locked calibration values in `CALIBRATION.md` with driving evidence (`implementation-plan.md §4.4`): Extractor token budget, retry count, completeness floor, jury threshold (asymmetric), refinement caps, auto-accept proposal cap.

**Exit criteria:**

- `cyberlab-gen extract` produces a valid AttackSpec for ≥4 of 5 curated blogs in N=3 runs; Layer 1 pass rate ≥95%; completeness scores cluster in a defensible band (the headline `implementation-plan.md §4.5` criteria).
- Asymmetric jury-threshold calibration documented in `CALIBRATION.md` with evidence.
- Eval reports archive cleanly to `eval/reports/`.

**No discretion on:** Asymmetric calibration discipline; eval-before-extend (don't tune past the curated set's signal); including a long blog.

**Output notes:** Append a Task 8 entry to `dev/phase-1-execution-log.md`.

---

## Final integration check

After Tasks 0–8, run the **Phase 1 exit criteria from `implementation-plan.md §4.5`** and confirm each passes. That section is the single source of truth for Phase 1 done-ness; this brief deliberately does not duplicate the criteria, so if §4.5 evolves the check evolves with it.

If all green, tag `v0.2`. Phase 1 is complete; move to Phase 2.

---

## Sequencing summary

```
Phase 0 complete (envelope, Provenance, registries, catalogs — all green)
   ↓
Task 0 (architect doc edits)   ←─ maintainer task; do first, ~15 min
   ↓
   ├── Task 1 (AttackSpec inner blocks) ─────────┐
   ├── Task 2 (provider call surface)            │
   └── Task 3 (Ingestion)                        │
                                                 ↓
                          Task 4 (enrichment) ←─ needs Task 1
                                                 ↓
              Task 5 (Extractor + Jury) ←─ needs Tasks 1, 2, 3
                                                 ↓
   Task 6 (Validator L1 + refinement + orchestration) ←─ needs 1,3,4,5 (2 underlies)
                                                 ↓
   Task 7 (extract verb + interrupt) ←─ needs Task 6
                                                 ↓
   Task 8 (eval harness) ←─ needs Task 7
```

Tasks 1, 2, 3 can run in parallel after Task 0. Task 4 needs Task 1. Task 5 needs 1+2+3. Task 6 needs 1+3+4+5. Task 7 needs 6. Task 8 needs 7.

For single-agent execution, a sensible linear order is 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. For multi-agent parallel execution, the graph above is the constraint.

**On parallelism and dynamic workflows.** Tasks 1/2/3 are the only genuinely independent fan-out in Phase 1, and even they converge fast at Task 5. The bulk of Phase 1 is deep-sequential design work (the Extractor's provenance discipline, the retry/refinement split, the orchestration graph) with no test-suite oracle to converge a swarm against — the AttackSpec's "correctness" is exactly what these tasks are *defining*. This is the wrong shape for a hundred-agent workflow. The parallelizable, oracle-backed work in this project comes later: once the validator layers and a test corpus exist (Phase 3+), generating labs across many blogs in parallel — each validated against the five-layer validator — is the Bun-port shape. Defer dynamic-workflow fan-out to there.

---

## What's intentionally not in this brief

- The Planner, Generators, Critic, Repair Agent — Phases 2+.
- Validator Layers 2/3/5 — Phase 2+ (Layer 1 only here). Layer 4 is v2-deferred.
- The full refinement loop — Phase 4 (Phase 1 has a minimal Extractor→Jury coordinator only).
- The third review surface for material discrepancies — Phase 4 (Phase 1 records them in the report).
- MSRC/OSV/KEV/EPSS/cloud-bulletin enrichment — registered stubs only; Phase 1 enriches CVE (NVD) and MITRE.
- Telemetry submission — Phase 5.

If unsure whether something belongs in Phase 1, the default is: it doesn't. Phase 1 is the first running agent, not the whole system.

---

## Execution log template

Every task ends by appending an entry to `dev/phase-1-execution-log.md`. The first agent to complete a task creates the file from the template below; subsequent agents append.

```markdown
# Phase 1 execution log

A running record of what each Phase 1 task actually built, what surprised the
implementer, and what was deferred. Entries are append-only; each task's
implementer adds an entry at the end. Purpose: inform Phase 2's brief and
surface doc-vs-code drift, exactly as Phase 0's log did.

Keep entries terse. Two paragraphs per task is usually right; a long entry
suggests something worth promoting into a `dev/decisions/` ADR instead.

---

## Task <N>: <task name>

**Date:** YYYY-MM-DD
**Implementer:** <agent identifier or human name>
**Time taken:** <rough estimate>
**Commit:** <git SHA of the final commit for this task>

### What was built

<2-4 sentences. Files created, tests added, smoke checks that pass. Skip what
the brief already specified; focus on what it didn't.>

### Surprises and friction

<2-4 sentences. What took longer than expected, doc ambiguities, places where
the architecture's intent had to be inferred. Link any ADR that resolved a real
question.>

### Deferred to later phases

<Anything the implementer noticed but consciously didn't address.>

### Doc-improvement notes for the next brief writer

<Optional. What would sharpen Phase 2's brief based on what came up here.>

---
```

Two rules, same as Phase 0:

- The log is append-only. Never rewrite a prior entry. If an earlier task did something wrong, fix it in code and add a new entry that says so.
- Doc-improvement notes feed directly into Phase 2's brief. Treat them as material for that document.
