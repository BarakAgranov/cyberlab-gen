# cyberlab-gen — Implementation Plan

**Companion to:** `architecture.md`, `pipeline.md`, `agents.md`, `schema.md`, `validation.md`, `eval.md`.
**Document scope:** Sequenced phasing for building cyberlab-gen v1 from zero to release. For each phase: what to build, what to defer, what the eval harness measures at that point, what gets calibrated, what the exit criteria are, and what risks to watch for.

**Author:** Barak Agranov
**Status:** Living document. Update at each phase boundary with what was actually locked vs. what was planned.

---

## 0. How to use this document

This is a phased build plan, not a sprint plan. Each phase produces an independently runnable, eval-measurable subset of the system. The deliverable at the end of every phase is *runnable*; the deliverable at the end of v1 is the full architecture.

Three usage modes:

1. **Sequential read.** Read top-to-bottom once before starting Phase 0 to understand the trajectory. The phases interlock — what gets locked in Phase 1 is what Phase 2 builds against.
2. **Active reference.** During each phase, the matching section is the working checklist. Build inventory, calibration items, exit criteria — these are operational.
3. **Phase-transition review.** At each phase boundary, walk the exit criteria. Only proceed when all are met. The temptation to start the next phase while finishing the current one is the single biggest threat to this plan's discipline.

When this plan and the architecture disagree, the architecture wins. When this plan and reality disagree, update this plan with the evidence and the new decision.

---

## 1. Principles that apply across all phases

These are the operating disciplines that make incremental construction actually work. Violating any one of them quietly breaks the whole approach.

### 1.1 Vertical slices, not horizontal layers

Every phase produces a working end-to-end slice: Ingestion → some agent → some validation → eval measurement. The slice may be narrow (one agent, one validation layer, one cloud) but it is complete. The alternative — build all the framework, then all the agents, then all the validators — produces a long stretch of infrastructure work with nothing to measure.

### 1.2 Eval is built alongside, never after

Whatever gets built in a phase, the eval harness gains the matching coverage in the same phase. Deferring eval coverage means deferring evidence-driven decisions for that subsystem indefinitely.

### 1.3 Calibrate before extending

When a stage works on the eval set, lock its budgets and thresholds with empirical data *before* building the next stage. Re-tuning everything against a moving pipeline shape is how systems lose causality and engineer-time.

### 1.4 One parameter per eval cycle

Never tune the prompt, the threshold, and the budget simultaneously. You can't attribute the result to any of them. Discipline yourself to A/B comparisons even when it's slower.

### 1.5 Curated set is a precious resource

Adding a blog is a real commitment — every future measurement runs against it. Don't pad the set to satisfy coverage requirements; add blogs only when they exercise patterns the current set doesn't.

### 1.6 Held-out set comes late

Building a held-out set before the pipeline is multi-stage and the curated set is well-explored is theater. Held-out integrity is meaningless when you can trivially read all your own blogs. Hold off until Phase 4.

### 1.7 Every phase produces a release tag

`v0.1`, `v0.2`, etc. Each tag's `CALIBRATION.md` is preserved. The phase's empirical decisions are recorded with the evidence that drove them. When in twelve months you look at "why is the Extractor jury threshold 0.65 and not 0.7?", the answer is findable.

### 1.8 The `/dev` directory

From day one. Scratch notes, prompt iterations that didn't work, parameter sweeps with results, weird LLM outputs that informed decisions. Not the architecture; the *log of how decisions got made*. This is the cheapest discipline available and the one most likely to be skipped.

### 1.9 When in doubt, defer

The architecture documents have a `v1.5+` and `v2` deferral discipline that is mature. Honor it. If something feels like scope creep in a phase, it almost certainly is.

---

## 2. The phase model at a glance

| Phase | Verb at end | Curated set | Held-out | Primary new agent | Primary lock |
|---|---|---|---|---|---|
| 0 — Skeleton | `--version` | 3 | — | — | Tooling baseline |
| 1 — Extractor | `extract` | 3–5 | — | Extractor + Jury | Extractor budget, completeness floor |
| 2 — Planner | `plan` | 8–10 | — | Planner + Jury | Reproducibility derivation, dep failure default |
| 3 — Generators (AWS only) | `generate` (AWS) | 12–15 | — | 4 Generators | Generator budgets, Layer 3 floors |
| 4 — Critic, refinement, multi-cloud | `generate` (full) | 18 | 12 | Critic | Refinement caps, stopping strategy |
| 5 — Fix, telemetry, polish | all four | same | same | Repair Agent | Fix budget, multi-model jury value |
| 6 — Release | same | same | rotated | — | Final calibration, pre-release budget run |

Phases run sequentially. Within a phase, work can parallelize at the engineer's discretion (or in this project's case, at the implementing agent's discretion across separate tasks).

---

## 3. Phase 0 — Skeleton

**Goal:** Everything that has to exist before any agent runs. Nothing that solves a user problem; everything that solves a future-engineer problem.

### 3.1 Scope

This is the phase that's most tempting to skip or do badly. It's pure infrastructure. But every shortcut here compounds across every subsequent phase — a missing test harness in Phase 0 becomes "we don't have regression tests" in Phase 4.

### 3.2 Build inventory

**Repository structure.**

```
cyberlab-gen/
├── cyberlab_gen/                # Python package
│   ├── __init__.py
│   ├── cli/                     # CLI entry points
│   ├── framework/               # Deterministic orchestration code
│   ├── agents/                  # Agent contracts (empty stubs in P0)
│   ├── schemas/                 # Pydantic models
│   ├── providers/               # Provider abstraction
│   ├── registries/              # Registry merge logic
│   └── state/                   # Local state directory management
├── registry/                    # Bundled YAML registries (mostly empty in P0)
│   ├── value_types.yaml
│   ├── facets.yaml
│   ├── external_data_sources.yaml
│   ├── static_catalogs.yaml
│   └── lab_credentials.yaml
├── eval/
│   ├── blog-sets/
│   │   └── manifest.yaml        # The 3 P0 curated blogs
│   ├── runner/                  # Eval harness code (empty in P0)
│   └── reports/                 # Eval report archive
├── docs/                        # The six architecture documents
├── dev/                         # Scratch notes, prompt iterations
├── tests/                       # pytest tests
├── pyproject.toml               # with `requires-python = ">=3.13"` (PEP 695 generics used in schema-details.md)
├── README.md
└── CALIBRATION.md               # Locked decisions log, empty in P0
```

**CLI scaffolding.** The four verbs from `architecture.md §2.1` exist as stubs:

- `cyberlab-gen generate <url>` — prints "not implemented in P0"
- `cyberlab-gen fix <lab-dir>` — same
- `cyberlab-gen validate <lab-dir>` — same
- `cyberlab-gen telemetry submit` — same
- `cyberlab-gen --version` — works, returns `0.0.0`

Use `click` or `typer`; don't write your own argument parser.

**Provider abstraction.** Per `pipeline.md §3.5`. Build the full abstraction even though you have one provider.

- `Provider` abstract base class with `complete(messages, output_schema, ...) -> StructuredOutput`.
- `AnthropicProvider` implementation (use `anthropic` SDK directly; do not build on `langchain` or similar — too much indirection for what you need).
- Capability hints: `high_quality_reasoning`, `fast_cheap_structured_output`, `long_context_extraction`. Map each to a default model in the ranking file.
- Ranking file at `cyberlab_gen/providers/model_rankings.yaml`. For P0, just Anthropic Claude models.
- Per-model cost tracking with a `TokenUsage` dataclass and a `CostLedger` that accumulates per agent.
- A `--max-llm-cost` CLI flag plumbed through to the cost ledger.
- Mock provider for tests: returns canned responses without making API calls.

**Pydantic models for the envelope.** Build only what Phase 1 will use:

- `IngestionResult`: `{url, canonical_url, content_hash, fetched_at, fetch_method, word_count, publisher_domain, cached_path}`.
- `AttackSpec`: top-level envelope with `spec_version`, `spec_kind`, `source`, `extraction_outcome`, `extras` placeholder. Inner blocks are stubs to be filled in Phase 1.
- `ProvenanceMetadata`: `{value, source, citations, confidence, requires_user_confirmation}` per `schema.md §4.9`. Generic over the value type.
- `CitationBlock`: `{kind, reference, location}` — kind discriminates blog passage vs. API response.

**Local state management.** Per `pipeline.md §3.6` and `architecture.md §2.2`:

- `~/.cyberlab-gen/config.yaml` — load user config, create default if missing.
- `~/.cyberlab-gen/cache/` — content-hash-keyed blog cache directory.
- `~/.cyberlab-gen/runs/<run-id>/` — per-run working directory.
- `~/.cyberlab-gen/reports/` — telemetry reports directory.
- `~/.cyberlab-gen/registry-overlay/` — user overlay directory.

A single `LocalState` class that knows all paths and creates directories on demand. Tested.

**Registry merge logic.** Bundled (read-only) + overlay (writable), per `schema.md §4.11`:

- Load YAML from both locations.
- Overlay wins on name collisions.
- Validation: every registry file conforms to its meta-schema (each entry has the required fields per the entry shapes in `schema.md §4.12`-§4.14).
- Empty registries are valid; P0 ships mostly-empty registries.
- Note: `external_data_sources.yaml` and `static_catalogs.yaml` are separate registries with the same entry shape but different semantic roles (per `schema.md §4.14`). The loader treats them symmetrically; agent-facing tools route to the appropriate one.

**Test harness.** Pytest, in `tests/`:

- `tests/unit/` — unit tests for framework code, schema validation, provider abstraction.
- `tests/integration/` — small end-to-end checks (mock provider, fake blog content, assert AttackSpec envelope produced).
- `tests/eval/` — placeholder, used in Phase 1.

CI workflow on GitHub Actions (or whatever you're using) runs pytest on every push.

**Three curated blogs in `eval/blog-sets/manifest.yaml`.** Hand-pick three that cover three different shapes:

- One AWS-targeted TTP chain (e.g., something simple like IAM privilege escalation via PassRole).
- One supply-chain-style blog (npm or GitHub Actions).
- One incident-analysis blog with defender techniques present.

Read all three carefully yourself before checking them in. Note your manual reading of each in `/dev/curated-blog-walks/<blog-id>.md` — what the chain steps are, what the value types are, what the facets should be. This is your manual ground truth for Phase 1.

**Documentation.** A `README.md` that explains the project at a level appropriate for v0.0 (basically: "this is being built; see docs/ for architecture"). A `CONTRIBUTING.md` shell.

### 3.3 Out of scope for Phase 0

Everything else. Specifically not:

- LangGraph orchestration. Build it when you have stages to orchestrate.
- Any real agent. The provider abstraction lets you make API calls; you have no use for them yet.
- Telemetry submission. Local report writing is also deferred to Phase 5.
- The refinement loop coordinator.
- Any validation layer.
- Any registry content beyond the minimal entries needed to validate the registry shape (a couple of example entries are enough).

### 3.4 Eval coverage in Phase 0

Smoke tests only. Five checks:

1. The CLI starts and prints help.
2. The provider abstraction successfully calls the mock provider and returns a parsed structured output.
3. The registry merge correctly merges a bundled entry with an overlay entry (overlay wins on collision).
4. **Every registry YAML file loads cleanly through its Pydantic meta-schema.** A pytest case per registry: read `registry/<registry_name>.yaml`, parse the YAML, validate against the Pydantic model from `cyberlab_gen.schemas.registries`, assert no errors. Covers `value_types`, `facets`, `external_data_sources`, `static_catalogs`, `execution_contexts`, `lab_credentials`, and each closed bundled-only catalog (`detection_components`, `severity_levels`, `detection_formats`, `provisioning_mechanisms`, `thesis_types`), which now have Pydantic models in `cyberlab_gen/schemas/catalogs.py` (ADR 0016) and whose bundled seeds are smoke-checked the same way in `tests/unit/schemas/test_catalogs.py`. This test is the mechanical guarantor of the schema-vs-registry consistency that registry-details.md and schema-details.md describe — without it, the two documents can silently drift, and the kind of bug Phase 0's review caught (registry YAML shapes failing to match the Pydantic models) would slip into Phase 1.
5. **Pricing coverage matches the ranking file.** Per `provider-interface.md §13.4`: load `cyberlab_gen/providers/pricing.yaml`, load `model_rankings.yaml`, assert every `(provider, model)` pair referenced anywhere in the ranking has a corresponding pricing entry. A missing pricing entry doesn't break lab generation (cost just reports as zero for that model), but it does silently break cost tracking — the test surfaces the omission at Phase 0 instead of in production.

These are in `tests/integration/`, not `tests/eval/`.

### 3.5 Calibration items locked in Phase 0

None empirical — Phase 0 has no production output. What gets locked:

- The provider interface signature. Changing it later means rewriting every agent. Get it right now.
- The Pydantic envelope shapes. Same story.
- The registry meta-schemas. Changing them means re-validating every entry ever added.

### 3.6 Exit criteria

- `cyberlab-gen --version` works.
- `pytest` runs clean.
- CI passes.
- The mock provider returns parsed structured output for at least one test case.
- All four CLI verbs exist as stubs (return clear "not yet implemented" messages).
- All five registry files load (even if mostly empty) **and validate against their Pydantic meta-schemas** (per §3.4 check 4).
- The `LocalState` directory layout creates correctly on a fresh machine.
- The three curated blogs are checked in with manual walks in `/dev/`.

Tag the repo `v0.1`. Move to Phase 1.

### 3.7 Risks

- **Over-architecting the provider layer.** It's tempting to add streaming, retries, circuit breakers, etc. P0 needs `complete()` and a cost ledger. The rest comes in Phase 5 or when needed.
- **Choosing the wrong orchestrator.** LangGraph is the assumption in `architecture.md §1.5`. Verify it can do what you need by sketching the Phase 1 pipeline (Ingestion → Extractor → Jury) on paper before committing. The cost of switching orchestrators in Phase 3 is much higher than the cost of changing your mind in Phase 0.

---

## 4. Phase 1 — Extractor + Jury

**Goal:** Read a blog, produce a validated AttackSpec. No planning, no generation. Just extraction.

### 4.1 Scope

This is the first phase where an actual agent runs. By the end of Phase 1, `cyberlab-gen extract <url>` writes an `attack-spec.yaml` to a working directory.

The architecture's "single source of truth is the manifest" doesn't apply yet — the manifest doesn't exist. The AttackSpec is the only structured artifact in Phase 1.

### 4.2 Build inventory

**Ingestion stage.** Per `pipeline.md §3.2.1`:

- URL fetcher with reasonable timeouts (10s default, configurable).
- Content normalizer (HTML → text; preserve heading structure as markers).
- Content hasher (SHA-256 of normalized text).
- Cache writer (write to `~/.cyberlab-gen/cache/<blog-hash>/`).
- Metadata recorder (URL, canonical URL, fetched-at, fetch method, word count, publisher domain).

Failure modes: URL unreachable → fail with clear message. Paywall detection (HTTP 403, very-short body) → fail with clear message. Bot-detected (Cloudflare interstitial, etc.) → fail with clear message. Don't try to bypass.

**Extractor agent.** Per `agents.md §5.4`:

- Pydantic AI agent with the AttackSpec schema as output type.
- Prompt loaded from `cyberlab_gen/agents/extractor/prompt.md`. Versioned with the code; iterate in `/dev/extractor-prompt-iterations/` first.
- Tools: `external_lookup(source_id, params)` for `external_data_sources` registry; `propose_value_type`; `propose_facet` (for `target:*` and blog-derived `lab_class_signal:*`).
- Search-before-claim discipline enforced at the framework level: agent traces are checked for tool-call evidence corresponding to every `source: external_api` field.

**External data sources (subset for Phase 1):**

- NVD (CVE lookups). No auth, rate-limited.
- MITRE ATT&CK (technique data). Static JSON.
- GitHub API (repo metadata). Optional token for higher rate limit.

Other entries from `schema.md §4.14` (MSRC, OSV.dev, AWS/Azure/GCP security bulletins, KEV, EPSS) are stubs registered in the registry but not yet integrated. Their absence is honest in `unknown_from_blog.reason`.

The `static_catalogs` registry is registered but empty in Phase 1; it's used by the per-phase Generator in Phase 3.

**Pre-Planner enrichment (skeleton).** Per `pipeline.md §3.2.4`:

- Framework code that walks `enrichment_triggers` from `external_data_sources` entries.
- Phase 1 only enriches CVE references (NVD) and MITRE technique references.
- The materiality check distinguishing material from non-material discrepancies (per `pipeline.md §3.2.4`) — implement it now. Material discrepancies populate a separate `material_discrepancies` field in the AttackSpec for Phase 1; Phase 4 wires this into the post-Extractor interrupt as a third review surface.

**Extractor-Jury agent.** Per `agents.md §5.5`:

- Pydantic AI agent with a `JuryVerdict` output schema: `{verdict, scores, feedback, retry_recommended}`.
- Prompt at `cyberlab_gen/agents/extractor_jury/prompt.md`.
- Tools: same as Extractor (for independent verification of `external_api` provenance).
- Verdict semantics:
  - `approve` → continue.
  - `revise` with field-targeted feedback (1-3 fields with citation problems) → Extractor re-runs targeting those fields.
  - `reject` (>30% of content fields with mismatched citations, indicating systematic hallucination) → halt.
- Asymmetric threshold calibration discipline noted in `CALIBRATION.md`: tune *up* on false-approval; never tune *down* on false-rejection.

**Refinement coordinator (minimal v1).** Just enough to handle the Extractor → Jury cycle:

- After Jury verdict, if `revise`, re-run Extractor with structured feedback wrapped in a `UserFeedback`-like object.
- Counts per-agent iterations against a per-agent cap (placeholder: 3 iterations in P1; revisit in Phase 4 when the full refinement loop comes online).
- On cap exhaustion, ship the last AttackSpec with `low_jury_confidence: true` flag and unresolved feedback in the run report.

**Validator Layer 1 (skeleton).** Per `validation.md §6.4`:

- JSON Schema validator over the AttackSpec.
- Registry reference resolution (every type/facet referenced exists in the merged registry).
- `spec_kind` discriminator enforcement.
- Failure routing: Layer 1 failures go to the responsible agent's retry mechanism, *not* refinement (per `validation.md §6.10`). Implement this distinction even though Phase 1 has only one agent — getting the retry/refinement split right early matters.

**Post-Extractor interrupt (interactive mode).** Per `pipeline.md §3.2.5`:

- `--interactive` mode (default). After Jury approval, pause. Show the AttackSpec. Four-option menu:
  1. Approve.
  2. Provide natural-language feedback → Extractor re-runs.
  3. Edit in `$EDITOR`.
  4. Abort.
- Per-proposal menu for any proposed value types or facets: Accept or Edit. Edited proposals are revalidated; structurally invalid edits reopen the editor with errors as comments.
- Phase 1 doesn't yet have the third review surface for material discrepancies; that comes in Phase 4 when enrichment is full-featured. For now, material discrepancies are listed in the run report only.
- `--auto` mode: skip all interrupts; auto-accept all proposals (up to the per-run cap).

**Eval harness Phase 1 additions.** Per `eval.md §7.3`:

- Per-blog eval runner that invokes the Extractor pipeline N times and records metrics.
- Metrics from `eval.md §7.4` available in Phase 1: Layer 1 pass rate, cost per AttackSpec, structural completeness score, registry proposals issued, `extras` entries count.
- Manual jury-decision review tooling: maintainer reads the AttackSpec, marks each jury verdict as correct/false-approval/false-rejection.
- `eval.md §7.5` calibration: false-approval and false-rejection rates per blog.

### 4.3 Curated set in Phase 1

Grow to 3–5 blogs. The 3 from Phase 0 plus up to 2 more if you encounter blog shapes your set doesn't cover.

### 4.4 Calibration items locked in Phase 1

- **Extractor token budget** (input + output). Locked from observed usage on the curated set.
- **Extractor per-stage retry count** (default 3). Validated against observed structural-failure rates.
- **Completeness floor** (default 0.5 per `agents.md §5.4`). Calibrated against the curated set's natural completeness distribution.
- **Extractor-Jury threshold** (default 0.7). Calibrated against false-approval and false-rejection rates with asymmetric discipline.
- **Refinement caps for the Extractor/Jury cycle** (placeholder 3 iterations, to be revisited in Phase 4 when full refinement loop is built).
- **Per-run cap on auto-accepted proposals** (placeholder 5).

Record each locked value in `CALIBRATION.md` with the evidence that drove it.

### 4.5 Exit criteria

- `cyberlab-gen extract <url>` produces a valid AttackSpec for at least 4 of 5 curated blogs in N=3 runs.
- Layer 1 pass rate ≥ 95% on the curated set.
- Completeness scores cluster in a defensible band on the curated set.
- Asymmetric jury threshold calibration is documented in `CALIBRATION.md` with the evidence.
- `--auto` and `--interactive` modes both work; the four-option interrupt menu functions; the per-proposal Accept/Edit menu functions; proposal edits are revalidated.
- The materiality-check code path in pre-Planner enrichment is implemented and produces material/non-material discrepancy records (even though Phase 1 doesn't surface them at an interrupt yet).
- Eval reports archive cleanly to `eval/reports/`.

Tag `v0.2`. Move to Phase 2.

### 4.6 Risks

- **Extractor prompt sprawl.** The Extractor sees the entire blog plus all extraction rules plus all search-before-claim disciplines plus all provenance rules. The prompt will balloon. Discipline yourself to overlay-vs-base prompt structure (per `pipeline.md §3.5`). The base prompt is short and stable; overlays carry model-specific tweaks.
- **Jury overconfidence.** The Jury is reading the same blog as the Extractor and is the same model family. They will agree more often than is healthy. The eval-harness false-approval rate is your only honest signal here; don't trust intuition.
- **External API rate limits.** NVD without an API key has a small budget. The first eval-harness run on the curated set may hit it. Build retry-with-backoff per `pipeline.md §3.7` early.
- **Long-blog handling.** The eval set should include at least one long blog. If chunking doesn't work cleanly, that's a Phase 1 finding worth surfacing now, not a Phase 4 surprise.

---

## 5. Phase 2 — Planner + Jury

**Goal:** Consume an AttackSpec and produce a draft LabManifest. Still no generation, no validation beyond Layer 1 + cross-block Layer 2.

### 5.1 Scope

Phase 2 builds the second major agent and the second jury. By the end, `cyberlab-gen plan <attack-spec.yaml>` produces a `lab-manifest.yaml` skeleton — phases, lab resources, prereqs, inputs, outputs, facets — with no code paths.

Phase 2 is where the manifest's role as "single source of truth" starts to matter. Every Phase 3+ agent reads it. The shape decided here is locked.

### 5.2 Build inventory

**Planner agent.** Per `agents.md §5.7`:

- Pydantic AI agent. Input: enriched AttackSpec + user config. Output: `LabManifest` skeleton.
- Tools: same as Extractor (external_data_sources for any planning-time lookups); `propose_facet` (for `runtime:*` and lab-derived `lab_class_signal:*` only); `query_value_types_registry`.
- The Planner does **not** propose value types (per `agents.md §5.7`); if it needs one not in the AttackSpec, that's an Extractor gap and routes back to the Extractor.
- The Planner does **not** repair AttackSpec content (per `agents.md §5.7`). AttackSpec incoherence routes back to the Extractor via refinement.
- Per-step reproducibility carried forward from the AttackSpec without re-evaluation. The Generator (Phase 3) will not re-evaluate either — classification authority is locked at the Extractor.
- Manifest `core.reproducibility` derived per the any-heterogeneity-mixed rule from `schema.md §4.8`. The derivation function is small framework code; the Planner emits per-step values and the derivation runs after.

**Manifest schema (full).** Build out the Pydantic models for every Manifest block:

- `CoreBlock` with the structured `reproducibility` block (mirrors AttackSpec shape per `schema.md §4.4`).
- `FacetReference`.
- `PrereqBlock` (pre_lab / mid_lab split).
- `InputBlock`.
- `LabResourceBlock` — including the `lab_role` list (values: `attack_target`, `attacker_infrastructure`, `defender_infrastructure`, `neutral`) and optional `role_notes` dict per `schema.md §4.4`.
- `PhaseBlock`:
  - `bind_inputs`, `outputs`, `produces_world_state`.
  - `produces_world_state` entries with the `identifier_kind: static | runtime_generated` discriminator per `schema.md §4.5`. For `static`: `identifier` field. For `runtime_generated`: `identifier_source` field pointing into the phase's output dict.
  - `on_dependency_failure` with default `warn` (per `schema.md §4.5`).
  - `step_composition`, `execution_context`, `provisioning_mechanism`.
  - `steps` (list of `StepBlock` from Phase 1).
- `OutputBlock`.

**Planner-Jury agent.** Per `agents.md §5.8`:

- Pydantic AI agent. Verdict shape mirrors Extractor-Jury.
- Reviews: Planner decisions trace to AttackSpec content; phases derivable from chain steps; lab_resources implied by chain or blog mentions; facet proposals justified.
- Asymmetric calibration discipline (same as Extractor-Jury).

**Pre-Planner enrichment (full).** Wire in the remaining external_data_sources that were stubs in Phase 1: MSRC, OSV.dev, KEV, EPSS, security bulletins. Each becomes a real triggered lookup. The materiality classification per source now uses the `discrepancy_materiality_rules` field on the source's registry entry.

**Validator Layer 2 (skeleton).** Per `validation.md §6.5`:

- Cross-checks between manifest blocks (e.g., phase `bind_inputs` reference declared phase outputs).
- Facet `implies` enforcement. Missing implied facets are *flagged as findings* — Layer 2 does not mutate the manifest (per `validation.md §6.5`). The finding routes to the Planner for re-run.
- Facet `incompatible_with` enforcement.
- `references_lab_outputs` cross-check, both directions: per-phase IaC references existing lab outputs (Lab-level Generator failure) AND per-phase IaC references existing `lab_resources` (per-phase Generator failure).
- `produces_world_state.identifier_source` resolution: for every `identifier_kind: runtime_generated` entry, verify the source path resolves to a declared phase output.
- `affected_platforms` consistency check (if present, must match `target:*` facets).

In Phase 2 there is no per-phase code yet, so Layer 2's code-vs-manifest checks are inert; the cross-block-within-manifest checks are live. Build the full Layer 2 framework now; the code-vs-manifest checks light up in Phase 3.

**Post-Planner interrupt.** Per `pipeline.md §3.2.8`:

- Same four-option menu as post-Extractor.
- Per-proposal menu for Planner-emitted facet proposals (Accept/Edit; edits revalidated).
- In Phase 2, no `references_lab_outputs` exists yet (no code), so that surface is inert; the LabPlan and proposal surfaces are live.

**Refinement coordinator (extended).** Now handles:

- Extractor ↔ Extractor-Jury (from Phase 1).
- Planner ↔ Planner-Jury (new).
- Planner failure → route back to Extractor (when AttackSpec coherence is the issue).
- Per-agent caps still placeholder, total cap still placeholder. The full coordinator with cycle detection and cascade handling comes in Phase 4.

**Eval harness Phase 2 additions.**

- Manifest field coverage metric (`eval.md §7.4`).
- Per-step reproducibility distribution metric.
- Lab-level reproducibility classification metric (using the any-heterogeneity-mixed rule).
- Planner-Jury false-approval / false-rejection review tooling.

### 5.3 Curated set in Phase 2

Grow to 8–10 blogs. Add blogs that exercise:

- Multi-cloud (at least one).
- Vulnerability-disclosure with substantive `vulnerability_story`.
- A blog that produces a `mixed` reproducibility classification (some `full` phases, some `demonstration_only`) so the derivation rule is exercised.
- A blog that requires the Planner to propose at least one `runtime:*` facet (e.g., a Cloudflare or Vercel attack).

### 5.4 Calibration items locked in Phase 2

- **Planner token budget.**
- **Planner per-stage retry count.**
- **Planner-Jury threshold** (asymmetric calibration).
- **Default `on_dependency_failure` value** confirmed as `warn` post-evidence.
- **Per-run cap on auto-accepted proposals** revisited (now Planner contributes facet proposals too).
- **Pre-Planner external API budget per run** (default 100; calibrate against observed usage).

### 5.5 Exit criteria

- `cyberlab-gen plan` produces a valid LabManifest for ≥4 of 5 Phase 1 blogs and at least 2 of the Phase 2 additions.
- Layer 1 + Layer 2 cross-block checks pass on ≥90% of curated runs.
- Lab-level reproducibility classification is correct per the any-heterogeneity rule on every test case.
- `lab_role` lists populate sensibly on lab_resources for at least one multi-role example (e.g., a logging bucket that's both `defender_infrastructure` and `attack_target`).
- The Planner correctly routes back to the Extractor when AttackSpec coherence problems are encountered (not "repairs" them).
- The materiality-based discrepancy classification works on real blog/API disagreements observed in the curated set.
- `CALIBRATION.md` records Planner-Jury threshold with evidence.

Tag `v0.3`. Move to Phase 3.

### 5.6 Risks

- **Manifest schema instability.** The temptation in Phase 2 is to "improve" the manifest shape when something feels awkward. Resist. The manifest shape was carefully designed; tweaks at this stage compound across Phase 3-5 rework. If something feels wrong, add it to `/dev/manifest-friction.md` and revisit at Phase 4 review.
- **Planner doing too much.** A Planner that "fixes" AttackSpec problems instead of routing back will *appear to work better* in early eval. It will be undebuggable in Phase 4. Hold the boundary.
- **`identifier_kind` decisions.** The Planner has to decide for each `produces_world_state` whether the identifier will be static or runtime-generated. This is a real judgment call that doesn't always have a clean answer (some identifiers are "static-with-environment-substitution"). Add `/dev/identifier-kind-edge-cases.md`.

---

## 6. Phase 3 — Generators (AWS only)

**Goal:** End-to-end generation pipeline for AWS-targeted labs. By the end, `cyberlab-gen generate <url>` produces a runnable AWS lab.

### 6.1 Scope

Phase 3 is the largest phase. Four agents (per-phase, Lab-level, Cleanup, Docs), three validator layers (1, 2, 3, 5), the lab directory structure on disk, and the eval harness extensions to measure code quality.

AWS only. Azure and GCP come in Phase 4. The single-cloud restriction in Phase 3 keeps Layer 3 (containerized dry-run) manageable — one cloud SDK in the container, one IAM catalog to integrate, one set of tflint rules.

### 6.2 Build inventory

**Per-phase Generator agent.** Per `agents.md §5.9`:

- Pydantic AI agent. One instance per phase. Output: a directory tree of generated files (Python module, optional Terraform, per-phase cleanup.sh).
- Tools:
  - `lookup_cloud_iam_action(cloud, action)` — single signature with cloud as parameter (per `agents.md §5.9`). Consults the AWS IAM catalog from `static_catalogs`. Azure and GCP catalogs are stubs in Phase 3; only AWS is live.
  - `web_search` — for current Terraform/cloud syntax, version lookups.
  - Read access to manifest, value_types entries, canonical lab-credentials catalog.
- Quality bar from `agents.md §5.9`: imports minimal (cloud SDK per phase's `runtime:*` facet only), planted credentials use canonical catalog patterns, `produces_world_state` entries populated with correct `identifier_kind`.
- Parallelism: framework computes phase DAG from `bind_inputs` and `produces_world_state` overlap (per `pipeline.md §3.2.9`); independent phases run concurrently.
- Failure mode: if the Generator hits a capability boundary preventing implementation at the declared reproducibility tier, it fails the stage with structured feedback. Does not re-classify; routes back to the Planner.

**Lab-level Generator agent.** Per `agents.md §5.11`:

- Pydantic AI agent. Runs serially after all per-phase Generators complete.
- Output: `setup.sh`, lab-level IaC (`infra/main.tf`), entry-point script (`attack/main.py`).
- `setup.sh` supports `--from-phase` and resolves prerequisite phases.
- **Cleanup-confidence mechanical gate.** When the Critic's per-phase confidence for cleanup-relevant phases is below threshold (placeholder 0.5 per `architecture.md §0.5`), the generated `setup.sh` emits a startup check that refuses to run without `--accept-low-cleanup-confidence`. The check reads from `validation_report.json` so the gate is data-driven. In Phase 3, the Critic doesn't exist yet — the gate's framework wiring is built, and the threshold check uses a placeholder value of 1.0 (gate never fires); Phase 4 wires in the real Critic confidence.
- Lab-level IaC translates `lab_resources` declarations into actual Terraform resources, including their `lab_role` declarations (passed through as Terraform comments for human readability).

**Cleanup Generator agent.** Per `agents.md §5.12`:

- Pydantic AI agent. Runs after Lab-level Generator.
- Output: lab-level `cleanup.sh` (orchestrator) and `verify.sh`.
- Three-tier cleanup model per `agents.md §5.12`: inline try/finally in phase code (per-phase Generator), per-phase cleanup.sh (per-phase Generator), lab-level cleanup.sh (this agent).
- Reads `identifier_kind` from `produces_world_state` entries: hardcodes literal for `static`; reads from `identifier_source` at runtime for `runtime_generated`. Generated cleanup code must not hardcode runtime-generated placeholders.
- `verify.sh` confirms cleanup succeeded; does not check setup correctness.

**Docs Generator agent.** Per `agents.md §5.13`:

- Pydantic AI agent. Runs last.
- Output: root `README.md` (with the three-tier per-phase confidence presentation from `agents.md §5.13` — though in Phase 3 confidence is placeholder until Phase 4 brings the Critic online), `docs/attack_guide.md`, `docs/concepts.md`, `docs/attack_narrative.md`, `docs/real_world_examples.md`, `docs/prerequisites.md`, `docs/defender_techniques.md` (when applicable), `detection/mitre_mapping.md`, `detection/cnapp_mapping.md`.
- Quality bar from `agents.md §5.13`: no LLM-original technical claims. Every substantive technical claim grounded in AttackSpec, validation report, or web_search with citation.

**Validator Layer 2 (full).** Now that code exists, the manifest-vs-code cross-checks light up. Per `validation.md §6.5`:

- Function name matching (`step.function_name` exists in module).
- Declared output shape matching.
- World-state cleanup coverage.
- IaC resource declarations match manifest.
- `references_lab_outputs` contract verification.
- `identifier_source` path resolution for `runtime_generated` entries.

**Validator Layer 3.** Per `validation.md §6.6`:

- Container with Terraform, AWS CLI, Python, ruff, mypy, tflint with AWS plugin, tfsec, checkov, cfn-lint, shellcheck.
- Static analyzers split by category per `validation.md §6.6`: code-quality at conventional strictness; security-finding rules read `lab_role` from the manifest and treat findings on `attack_target` resources as informational rather than failing.
- Cloud-API hallucination cross-check against `static_catalogs` for every catalog-relevant identifier in generated code.
- Per-step reproducibility handling: `demonstration_only` steps get syntax-only validation.

**Validator Layer 5.** Per `validation.md §6.8`:

- Credential scanners (trufflehog, gitleaks). OSS only per `validation.md §6.8`.
- Canonical lab-credentials catalog whitelisting.
- Forbidden-pattern list (real AWS account IDs, real ARNs not in lab outputs, etc.).
- File-system scope check (pattern-based per `validation.md §6.8`).
- High-severity halts pipeline; medium-severity ships with flag.

**Output stage.** Per `pipeline.md §3.2.13`:

- Move files from working directory to user's target path.
- Lab directory structure per `pipeline.md §3.2.13`.
- `validation-report.md` at lab root.
- `.cyberlab-gen/` provenance directory.

**Eval harness Phase 3 additions.**

- Layer 2 and Layer 3 pass rates per layer.
- Per-cloud (AWS-only in P3) sub-metrics.
- Code-shape adherence to canonical examples.
- Cleanup coverage rate (declared world-state items addressed).
- Reproducibility-tier distribution validated against expected (per-blog manual ground truth).

### 6.3 Curated set in Phase 3

Grow to 12–15 blogs. Add AWS-focused blogs covering:

- IMDS exploitation.
- IAM privilege escalation paths.
- Lambda exploitation.
- S3 misconfiguration.
- Cross-account assumption attacks.
- At least one blog with intentionally vulnerable lab_resources (so Layer 3 intentional-misconfig handling is exercised).

### 6.4 Calibration items locked in Phase 3

- **Per-agent token budgets** for each of the four Generators.
- **Per-phase Generator budget per phase** (this multiplies for multi-phase labs).
- **Layer 3 static-analyzer severity floors** per analyzer per category.
- **Container image baseline** (Phase 3 builds the AWS-only variant; Azure/GCP added in Phase 4).
- **Cleanup-confidence gate threshold** stays at placeholder; calibrated in Phase 5 once real Critic data exists.

### 6.5 Exit criteria

- `cyberlab-gen generate <url>` produces a runnable AWS lab for at least 8 of 12-15 curated AWS-focused blogs.
- Layer 2 cross-checks pass on ≥80% of generations.
- Layer 3 (containerized dry-run) passes on ≥70% of generations.
- Layer 5 catches at least one accidental real-credential pattern in curated-set evaluation (validates the layer works).
- The intentional-misconfig handling works: tfsec fires on declared `attack_target` resources without failing the layer.
- Cleanup scripts work end-to-end on at least 3 blogs manually run by you against a real AWS account (this is a manual check; not the eval harness).
- `produces_world_state` with `identifier_kind: runtime_generated` produces cleanup that actually reads the runtime value (don't trust eval; manually verify on at least 2 cases).
- The three-tier confidence presentation framework is built (uses placeholder values pending Critic).

Tag `v0.4`. Move to Phase 4.

### 6.6 Risks

- **Cleanup correctness.** This is the hardest single problem in Phase 3. A lab that "passes Layer 3" can still leave orphaned resources if `identifier_kind` was misclassified or the cleanup script doesn't actually read `identifier_source` correctly. The `architecture.md §0.5` cleanup-confidence gate exists because we know this is unreliable. Manual verification on real AWS accounts (with disposable lab accounts) is non-negotiable here.
- **Per-phase Generator parallelism bugs.** Phase DAG computation has edge cases. A phase that fans out into multiple "independent" branches that secretly share `produces_world_state` will produce race conditions that don't show up in Layer 3. Build the DAG verification tooling early.
- **Intentional-misconfig false negatives.** A bug in `lab_role` handling could cause Layer 3 to relax strictness on resources that shouldn't be relaxed. Cross-check that Layer 3 *still fails* on a resource that's deliberately mislabeled as `attack_target` but has a *different* security problem.
- **Docs Generator hallucination.** This is the agent with the most freedom and the most user-facing output. The "no LLM-original technical claims" quality bar needs eval-harness teeth: maintainer manual review of Docs outputs against the AttackSpec + Critic web_search results, with ungrounded claims flagged.
- **Container image size.** With AWS SDK + Terraform + IaC tooling + scanners, the base image is multi-GB. Note in `dev/operations-debt.md` for Phase 5/6 to address before release.

---

## 7. Phase 4 — Critic, refinement loop, multi-cloud

**Goal:** Bring the Critic online, build the full refinement loop, extend Generators to Azure and GCP, build the held-out set.

### 7.1 Scope

Phase 4 transforms the pipeline from "extract → plan → generate → validate → ship" to the full architecture: Critic feedback drives refinement, multi-model juries are real, the held-out set provides generalization signal, and Azure/GCP join AWS as first-class runtimes.

This is also when the per-phase confidence values become real and the cleanup-confidence gate actually fires for low-confidence labs.

### 7.2 Build inventory

**Critic agent.** Per `agents.md §5.14`:

- Pydantic AI agent. Runs after all mechanical validators.
- Output: `QualityReport` with per-dimension rubric scores, per-phase confidence, structured concerns, verdict (`approve` / `refine` / `reject`).
- Tools: read access to all artifacts; `web_search` mechanically capped at 5 calls per run (per `agents.md §5.14`). Exceeding the cap fails the stage.
- Critic is advisory — never blocks shipping. A `reject` verdict after refinement exhaustion ships the lab with prominent rejection notice.
- Per-phase confidence values feed the README's three-tier presentation and the cleanup-confidence gate.

**Refinement loop coordinator (full).** Per `pipeline.md §3.2.12`:

- Consumes Validator report + Critic verdict.
- Routing table per `validation.md §6.10`: Layer 1 → retry (not refinement); Layer 2 → implementation agent; Layer 3 → file's responsible agent; Layer 5 high → halt; Critic refine → re-run per Critic's recommendations.
- Per-agent cap (placeholder 5) and total cap (placeholder 20). Per-agent is a fairness mechanism; total cap typically binds.
- Oscillation handling: cycle detection (coupled re-generation), phase-level repeat detection (cap-per-stage), cascade detection (route to upstream).
- Cycle-resolved pairs locked for remainder of refinement loop (per `pipeline.md §3.2.12`).
- Same-root-cause finding deduplication: Layer 2 and Critic findings on same artifact route once (per `validation.md §6.10`).
- Best-state retention: top-3 by combined validator+quality score plus most recent. Stored under `iteration_snapshots/`.

**Multi-cloud Generator extensions.**

- Azure SDK support in per-phase Generator's tooling (azure-mgmt-*, azure-identity).
- GCP SDK support (google-cloud-*).
- Per-cloud tflint plugins (ruleset-azurerm, ruleset-google) in the container.
- Azure RBAC catalog and GCP IAM permissions catalog wired into `static_catalogs` and into `lookup_cloud_iam_action`.
- Multi-cloud labs (labs with multiple `target:*` or `runtime:*` facets) generate correctly with per-cloud provider blocks.

**Pre-Planner enrichment full materiality logic.**

- `discrepancy_materiality_rules` from each `external_data_sources` entry consulted.
- Material discrepancies surface as the third review surface at the post-Extractor interrupt (per `pipeline.md §3.2.5`) in `--interactive`.
- Non-material discrepancies recorded in provenance, surfaced in run report only.
- `--auto` mode flags material discrepancies in the run report without interrupting.

**Multi-model jury support.** Per `pipeline.md §3.5`:

- Jury layer can specify a different provider/model than the agent it judges.
- If only one provider configured, jury uses a different model from same provider (degraded diversity, logged in run report).
- Add a second provider (OpenAI is the obvious choice) to the provider abstraction.

**Held-out set.** Per `eval.md §7.3`:

- Initial held-out set: 12 blogs.
- Coverage spans the same dimensions as the curated set but with no overlap.
- Maintainer (you) reads them once at construction time, then does not read them again outside of the paired-rotation discipline.

**Paired rotation policy.** Per `eval.md §7.5`:

- Implementing the rotation manifest in `eval/blog-sets/`.
- When a held-out blog gets manually reviewed (for calibration), it auto-rotates to curated at the next release.
- Telemetry counter for "held-out reviews consumed since last rotation."

**Eval harness Phase 4 additions.**

- Per-blog N=3 runs as the standard release-candidate cadence.
- Bootstrapped 95% confidence interval comparisons between strategies.
- Coefficient-of-variation flagging on primary metric + cost.
- Three stopping strategies wired in: fixed-N, score plateau, validator+Critic verdict (per `eval.md §7.7`).
- Cost-per-quality composite metric (Validator pass rates weighted by layer + Critic overall score).
- Per-PR proxy metric: jury-pass-but-Critic-fail rate (per `eval.md §7.5`).

**CI gates (full).** Per `eval.md §7.11`:

- On PR: N=1 or N=2 (decide based on observed false-alarm rate; document in `CALIBRATION.md`). Compare against main baseline. Block on threshold violations.
- On release candidate: N=3 across curated + held-out. Schema walk. Manual jury review with paired rotation.
- Periodic: N=5 on representative subset.

### 7.3 Curated set in Phase 4

Grow to 18 blogs total (Phase 4 size commitment per `eval.md §7.3`). Held-out: 12 blogs. The split is locked from this phase forward; future changes go through rotation, not resizing.

Coverage requirements per `eval.md §7.3` apply to the union. Maintain the coverage matrix.

### 7.4 Calibration items locked in Phase 4

- **Refinement loop caps** (per-agent and total).
- **Default stopping strategy** (decided based on cost-per-quality measurement).
- **Cleanup-confidence gate threshold** (now with real Critic data).
- **Per-phase confidence three-tier thresholds** (0.6 and 0.4 from `agents.md §5.13` validated against eval data).
- **Critic web_search cap** (placeholder 5 reviewed against actual usage; adjust if needed).
- **Coefficient-of-variation threshold** for high-variance flagging.
- **PR-time CI thresholds** for both N=1 and N=2 options (per `eval.md §7.11`).
- **Multi-model jury value measurement.** Does multi-model jury actually reduce false-approval/false-rejection meaningfully? If not, single-model jury is the v1 default.
- **Asymmetric calibration evidence trail** for both juries on full curated + held-out review.

### 7.5 Exit criteria

- `cyberlab-gen generate <url>` produces validated labs for ≥12 of 18 curated blogs and ≥8 of 12 held-out blogs at N=3.
- Refinement loop converges (doesn't oscillate) on the curated set with the chosen stopping strategy.
- Cycle detection, repeat detection, and cascade detection all fire correctly on at least one synthetic test case each.
- Multi-cloud labs generate for at least 2 curated blogs (one Azure, one GCP) and at least 1 cross-cloud (e.g., AWS + GitHub).
- Materiality-scaled discrepancy surfacing works on at least one real blog/API disagreement in curated.
- Paired rotation has been exercised at least once (a held-out blog was reviewed, was rotated to curated, the next release reflects the change).
- Cleanup-confidence gate fires on at least one low-confidence lab in eval and refuses to run without the flag.
- Held-out vs. curated performance gap is within the bootstrapped confidence interval on the primary metric.

Tag `v0.5`. Move to Phase 5.

### 7.6 Risks

- **Critic-Jury overlap.** The Critic and the juries cover overlapping concerns. Per `agents.md §5.20` ownership table, they have different primary owners for different concerns — but in practice you'll see redundant feedback. Use the §5.20 table as the resolution authority.
- **Refinement-loop infinite-spend.** Without tight cap enforcement, refinement burns the budget on edge cases. Total cap must be enforced *before* the next iteration starts, not after; budget-overrun interrupt fires in both modes.
- **Multi-cloud catalog drift.** AWS IAM catalog, Azure RBAC catalog, GCP IAM catalog are large and updated periodically. Stale catalogs produce false-positive hallucination findings. Build a catalog-update workflow with eval-harness validation.
- **Held-out contamination via review.** Paired rotation is the structural defense, but a careless maintainer reading held-out blogs casually destroys the integrity. The architecture's `eval.md §7.2` honest framing covers this; reinforce it in `CONTRIBUTING.md`.
- **Multi-model jury cost.** Multi-model juries double or triple per-jury cost. Measure the actual quality benefit before deciding it's worth the cost.

---

## 8. Phase 5 — Fix, telemetry, polish

**Goal:** Build the fix pipeline (Repair Agent), build the telemetry submission flow, polish all user-facing surfaces.

### 8.1 Scope

Phase 5 brings the fourth CLI verb (`fix`) online, builds the telemetry submission flow, and polishes everything user-facing for release readiness. This is also where Layer 3 auto-on-IaC-patches lights up, fix_history continuity is implemented, and the credential-paste detector becomes functional.

### 8.2 Build inventory

**Repair Agent.** Per `agents.md §5.16`:

- Pydantic AI agent. Conversational REPL session, not pipeline stage.
- Tools: `read_lab_file`, `list_lab_files`, `read_provenance`, `web_search`, the four output tools (`propose_patch`, `propose_doc_update`, `explain_environmental_issue`, `request_more_info`), credential-paste detector heuristic.
- Mechanical thresholds (per `agents.md §5.16`): patches ≤3 files per turn; manifest-touching patches require justification field. Exceeding halts the turn with structured feedback.
- No write access (framework applies patches after user approval), no cloud access, no lab execution. Build the boundary as framework-enforced, not prompt-instructed.
- Credential-paste detector: heuristic pattern matching against canonical lab-credentials catalog negation. When user paste matches real-credential heuristics but not canonical fakes, warn before continuing.

**Fix pipeline.** Per `pipeline.md §3.4`:

- One mode only: interactive REPL.
- Session startup loads minimal context per `pipeline.md §3.4.2`.
- Fix_history continuity check: compute file hashes of files referenced by prior fix_history entries; if any changed, mark prior history as "background context only" and surface in opening summary.
- Minimal validation on proposed patches per `pipeline.md §3.4.4`:
  - Layer 1 if manifest touched.
  - Layer 2 on declared-types or `references_lab_outputs` changes.
  - Layer 5 on every patch.
  - **Layer 3 auto-runs on IaC patches** (per `pipeline.md §3.4.4`); `--validate-patches-thoroughly` flag for non-IaC cases.
- `fix_history.json` persisted incrementally; written definitively on session exit.
- Separate budget from generation (placeholder pending Phase 5 calibration from observed usage).
- Cross-session continuity (next session reads prior history as background).

**Layer 5 fix_history handling.** Per `validation.md §6.8`:

- During fix patch validation: Layer 5 scans the proposed patch AND the new fix_history entry being written.
- During explicit `cyberlab-gen validate`: Layer 5 scans the entire lab including fix_history.json.
- Complementary to the Repair Agent's live credential-paste detector.

**Telemetry submission flow.** Per `pipeline.md §3.6`:

- `cyberlab-gen telemetry submit` lists queued reports, runs sanitization pass, shows side-by-side diff, asks confirmation, sends to endpoint.
- Sanitization scope per `pipeline.md §3.6.4`: API keys, cloud account IDs, user-specific identifiers, absolute file paths, anything in working dir not generated by cyberlab-gen. Includes fix_history.json content.
- Sanitization is best-effort with user confirmation as final guard (per `pipeline.md §3.6.4`).
- `--yes --no-confirm` flag for power users automating submission.
- `--no-telemetry` flag and `telemetry.enabled: false` config option to disable entirely.
- Endpoint may not exist in v1 release; the command handles "no endpoint configured" with manual-sharing-instructions message.

**`cyberlab-gen validate <lab-dir>`.** Per `architecture.md §2.1`:

- Runs all mechanical validation layers against an already-generated lab.
- Refuses old-schema artifacts with "regenerate from blog URL" message.
- Used primarily by CI and after manual lab edits.

**Provider failure handling (full).** Per `pipeline.md §3.7`:

- Transient failures: retry with exponential backoff, 3 attempts.
- Rate-limit (429): same retry strategy.
- Quota exceeded: hard fail with structured message.
- Mid-pipeline outage: checkpoint to `~/.cyberlab-gen/checkpoints/<run-id>/`; `cyberlab-gen resume <run-id>` resumes from checkpoint.
- Checkpoint-write failure: treated as hard fail; partial output preserved in working directory.
- Resume refuses across schema-version or tool-version mismatch.

**Eval harness Phase 5 additions.**

- Fix-session pattern aggregation (privacy-narrowed per `eval.md §7.9`): patch diffs, validation findings on patches, fix outcomes, recurring `unknown_from_blog.reason` strings. *Not* conversational content.
- Layer 5 finding-rate tracking over time (per `eval.md §7.12`).
- Sparse-telemetry early-period acknowledgment in reports (per `eval.md §7.9`).

### 8.3 Curated set in Phase 5

Stable at 18 + 12 held-out. Phase 5 doesn't grow the set; it tightens the existing one.

### 8.4 Calibration items locked in Phase 5

- **Fix-mode budget default** (placeholder calibrated against observed Repair Agent usage on real-world failure scenarios).
- **Repair Agent patch-size threshold** (placeholder 3 files; calibrated against observed patch sizes).
- **Multi-model jury value** (locked decision: single-model default or multi-model default).
- **Materiality-rule refinements** based on observed real blog/API discrepancies.

### 8.5 Exit criteria

- `cyberlab-gen fix <lab-dir>` works on at least 5 deliberately-broken curated labs (you intentionally break them in different ways and walk through fixing them).
- Fix_history continuity check fires correctly when a lab is modified between sessions.
- Layer 5 catches a user-pasted real-credential fragment in a fix session.
- Telemetry submission's sanitization preview shows the diff correctly; nothing in the redacted-out list leaks through.
- `cyberlab-gen validate` correctly refuses an old-schema lab.
- Checkpoint + resume work end-to-end on a mid-pipeline kill (you SIGTERM during generation, resume picks up).
- Documentation (`README.md`, `CONTRIBUTING.md`, `telemetry.md`) is release-ready.

Tag `v0.6`. Move to Phase 6.

### 8.6 Risks

- **Repair Agent scope creep.** The Repair Agent is the most "agentic" of the agents (long conversations, many decisions). It will be tempting to give it more tools. Hold the no-write/no-cloud/no-execute boundary.
- **Sanitization gaps.** Any pattern not caught by sanitization is a real leak. The mitigation is the user-visible diff, but novel credential formats slip through. Document the pattern list publicly so users can review and contribute.
- **Endpoint absence.** v1 may ship without a telemetry endpoint. That's fine, but the CLI should handle it gracefully and the architecture's framing of "telemetry is opt-in and explicit" should be preserved.
- **Fix budget calibration on real failures.** You won't have many real-failure cases until users start using the tool. The Phase 5 budget calibration is "informed placeholder," not "validated." That's noted in `CALIBRATION.md` for Phase 6 re-visit.

---

## 9. Phase 6 — Release

**Goal:** Pre-release calibration, final eval reports, documentation polish, release artifact production.

### 9.1 Scope

Phase 6 is the discipline that prevents shipping with "we know these are wrong" defaults. It includes the pre-release budget calibration commitment from `architecture.md §8.4`, the final eval reports across curated + held-out, and the release artifacts (PyPI package, container image, documentation site if any).

### 9.2 Build inventory

**Pre-release budget calibration.** Per `architecture.md §8.4`:

- Run `cyberlab-gen generate` on 2-3 representative curated blogs with current placeholder budgets.
- Measure actual LLM cost, actual per-agent token usage, actual refinement-loop iteration counts.
- Replace placeholder defaults with informed defaults:
  - `--max-llm-cost` default.
  - Per-agent token budgets in `agents.md §5.19`.
  - Total iteration cap and per-agent iteration cap.
  - Fix-mode budget default.
- Statistical validation does not happen here (sample is small); informed defaults do.
- Document in `CALIBRATION.md` what each new default replaces and what the evidence was.

**Recalibration release machinery.** Per `eval.md §7.13`:

- Document the "recalibration release" type in the release process.
- Bundle the calibration scripts so a future recalibration (triggered by new major model release) can be re-run mechanically.

**Final eval report.**

- N=3 across full curated set + N=3 across full held-out set.
- Schema walk (per `eval.md §7.10`) against the post-rotation curated set.
- All metrics from `eval.md §7.4` reported with bootstrapped confidence intervals.
- Cost-per-quality composite metric per stopping strategy.
- Layer 5 finding-rate baseline established (for `eval.md §7.12` drift monitoring).
- Coverage matrix per `eval.md §7.3`.
- Paired-rotation status (which held-out blogs have been consumed).

**Release artifacts.**

- PyPI package with proper metadata, dependencies, version pinning.
- Container image for Layer 3 — versioned, tagged, documented.
- Release notes covering: locked defaults, known limitations, the v1.5+ and v2 deferral list, pre-release calibration evidence.
- A `CHANGELOG.md` mentioning every locked decision from `CALIBRATION.md`.

**Documentation polish.**

- `README.md` for v1.0: installation, quick-start, link to architecture docs.
- `CONTRIBUTING.md` covering registry proposal flow, paired rotation discipline, prompt iteration discipline.
- `telemetry.md` documenting exactly what's collected, what's redacted, what's done with the data.
- Architecture docs reviewed for accuracy against actual implementation (the architecture is the contract; if anything drifted, document the drift or fix the code).
- A "for AI-implementing-agents" prologue (if relevant) that points future implementers at the architecture, this plan, and CALIBRATION.md as the canonical sources.

### 9.3 Curated set in Phase 6

Locked from Phase 4: 18 curated + 12 held-out. Apply paired rotation if any held-out reviews have accumulated.

### 9.4 Calibration items locked in Phase 6

The full set, replacing all placeholders that had observable usage in Phase 5:

- All token budgets per agent.
- `--max-llm-cost` default.
- Refinement loop total cap and per-agent cap.
- Fix-mode budget.
- Critic web_search cap.
- Per-phase confidence three-tier thresholds.
- Cleanup-confidence gate threshold.
- Default stopping strategy.
- Default jury thresholds (asymmetrically calibrated).
- All Layer 3 severity floors.
- Coefficient-of-variation threshold for high-variance flagging.
- Per-run cap on auto-accepted proposals.

Everything that was "placeholder pending data" in earlier phases now has a value with evidence.

### 9.5 Exit criteria

- All `architecture.md §8.4` placeholder items have informed defaults (pre-release calibration ones from evidence, post-launch ones from Phase 4-5 measurement).
- `CALIBRATION.md` lists every locked decision with its evidence link.
- Final eval report is in `eval/reports/`.
- PyPI package builds, installs, runs `cyberlab-gen --version` on a clean machine.
- Container image builds, runs Layer 3 on a sample lab.
- Manual end-to-end test: install fresh on a new machine, generate one lab, run it on a disposable cloud account, verify cleanup, run fix mode on a deliberately-broken version, submit telemetry. All steps work.
- Release notes pass a careful read for accuracy.
- Architecture docs reviewed against implementation; any drift is either fixed or documented.

Tag `v1.0`. Ship.

### 9.6 Risks

- **"Just one more change" before release.** This is the biggest risk in Phase 6. You will see things you want to fix. Most of them belong in v1.1, not v1.0. The architecture's `v1.5+` deferral discipline applies to your own impulses too.
- **Calibration evidence quality.** Pre-release calibration on 2-3 blogs is informed but not statistically validated. Document that honestly in release notes; don't claim more confidence than the evidence supports.
- **Container image size at launch.** If Phase 3-5 deferred the container-size operational concern, it surfaces here. Split-by-runtime images (one per cloud) is a meaningful improvement but a real engineering task. Decide if it's v1.0 or v1.1.
- **Telemetry endpoint coordination.** If you're running an endpoint, it needs to be operational at release. If not, the `telemetry submit` command needs to surface that gracefully and point to manual-sharing as the alternative.

---

## 10. After v1.0

The architecture's `v1.5+` and `v2` deferral lists in `architecture.md §8.2` and `§8.3` are the roadmap. Don't expand the v1.0 scope to include them; ship v1.0 and prioritize from real-world feedback.

The first months post-release are sparse-telemetry early period (per `eval.md §7.9`). The curated set is the dominant quality signal; telemetry-driven evolution kicks in gradually as adoption accumulates. Don't panic at low telemetry volume; it's expected.

Recalibration releases (per `eval.md §7.13`) are the operational mechanism when a new major model lands. They're calibration-only releases that re-run thresholds against the new model landscape; no architectural changes. Naming them as a release type makes their role explicit.

The architecture's distinguishing virtue is honest framing about what works, what doesn't, and what's uncertain. Preserve that through implementation and through post-release evolution. A v1.0 that's honest about its limits is more useful — and more trusted — than a v1.0 that overclaims.

---

*End of implementation plan. See `architecture.md` and its companions for the system being implemented; this plan describes the order of construction. When this plan and the architecture disagree, the architecture wins; when this plan and reality disagree, this plan gets updated.*
