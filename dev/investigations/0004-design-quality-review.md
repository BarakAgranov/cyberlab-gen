# 0004 — Design-quality & craftsmanship review: will Phase 2 be clean or painful to build on this?

**Date:** 2026-06-10
**Status:** **Report-only. No code, no `docs/`, no ADR, no contract is changed by this
document.** It judges design quality and ranks what to address before Phase 2; it proposes
nothing to implement. The maintainer reads this and decides which findings are worth acting on.
Priority labels (FIX-BEFORE-PHASE-2 / SHOULD-FIX / NICE-TO-HAVE) are triage judgments, not
work orders. HEAD = `4aa3db3`.
**Lens — and how this differs from 0003.** 0003 audited *deferrals* against one question (does a
lesser-path compromise the AttackSpec contract?). This is a *craftsmanship* review against a
different one: **the codebase already passes a strict gate** — `just verify` = ruff + ruff-format
+ pyright **strict** + pytest, green — so the machine-checkable layer is settled. This reports the
higher-order things a strict toolchain cannot catch (design coherence, coupling, the right
abstractions, error-handling philosophy, extensibility seams, maintainability, traceability),
weighted by one criterion: **Phase 2 will heavily extend this code** (Planner, four Generators,
Critic, Repair Agent; validator Layers 2/3/5; NVD/MITRE/OSV adapters; new registries; new pipeline
stages), so "is it well-built" largely means "will those additions be an *add* or a *modify*."
**Method.** A 10-reader fan-out over all layers (control-flow core; agent layer; enrichment +
validation; providers; registries + proposals; schemas; CLI + persistence + errors; tests;
cross-cutting coupling; cross-cutting observability), each tagging findings against the seven
rubric dimensions, **then every finding adversarially verified** by a second reader charged with
*refuting* it — re-reading the cited `file:line`, checking it is not already linter/pyright-covered,
and confirming the priority. 56 verified candidates survived; 3 were refuted and demoted (§5); a
completeness-critic pass then hunted for what the ten layers missed (§1 closes with its six
additions). The two most consequential novel claims (the eval provenance re-leak; the LLM-authored
`spec_version`) were **re-verified by hand** against the source before being written here. *(The
first fan-out hit a session limit mid-verification; it was re-run clean after the limit lifted —
the 14 findings it had fully verified all reappear below.)*
**Provenance:** reconstructed from a read of the code, the ADRs, the docs, and investigations
0001–0003 — **no run executed, no provider-backed eval (real money).** Each claim is `[V]`
(verified in code I or a verifier opened this session) or `[I]` (inferred).

## The headline (read this first)

**This is a genuinely well-built codebase at, and in places above, the 20-year-senior-engineer
bar. The verdict is not inflated and the praise is specific.** The load-bearing invariant — the
`architecture.md §1.5` LLM/framework split — is enforced *structurally in code*, not by convention:
agents return only typed content/judgments; every routing, retry-budget, and stop decision lives in
framework nodes; the two historically-acute false-provenance sites (`lineage.model`,
`proposed_by_model`) are fixed at the root off the billed ledger (ADR 0065); and **no new
LLM-content-as-provenance edge was found** in the shipping path. The targeted-patch refinement is a
textbook convergent-by-construction implementation; the cost subsystem (Decimal arithmetic,
per-attempt entries, ceiling-on-billed-failure) is exemplary; the schema layer's `ArtifactModel`
discipline, PEP-695 generics, and `Provenance[T]` pickle fix (ADR 0066) are idiomatic and
regression-tested; and the test suite is senior-grade — it asserts *behaviour and call-paths*, and
where a fake cannot be faithful it drops in a purpose-built typed double rather than asserting
against a fiction. The **graceful-vs-hard-fail asymmetry from 0001 is resolved**: NVD and MITRE now
degrade identically (honest skip / well-formed-passes-unverified), and the error taxonomy is a
coherent, consistently-applied policy, not ad hoc.

**Where it is weakest is exactly where Phase 2 plugs in.** The code is correct and well-tested for
the *one* pipeline and *two* agents that exist today; its **extension mechanism is "modify the
machine," not "register a part."** The orchestrator is a ~390-line closure factory that *is* the
state machine; there is no agent contract above `AgentRunner`, no validator-layer contract, no
stage abstraction, no provider dispatch, and the external-source enrichment hardcodes NVD/MITRE
branches while ignoring the data-driven adapter contract the registry already declares. Each of
those is fine for Phase 1 and an open/closed violation for Phase 2 — the point at which the agent
count goes 2 → ~10, the validator count 1 → 4, and the external-source count 0 → several. Separately,
the observability is *Extractor-shaped*: rich routing telemetry is computed and then dropped on
persistence, so a failed **multi-agent** run could not be reconstructed from what is captured.

**The fix-before-Phase-2 set is real but bounded**, and dominated by (a) three correctness-grade
items in the §1.5 family — one of them the single open must-fix 0003 already named — and (b) a small
number of seams worth converting from modify-to-extend *before* the duplication exists to fight. The
honest bottom line: **clean core, strong craftsmanship, a handful of structural seams to tighten
before the agent count multiplies.** Perfect shape would not mean zero findings; it means no finding
that compromises correctness or makes Phase 2 materially harder — and there are a few of each.

| Dimension | Health | Where it bites |
|---|---|---|
| 1. Coupling & boundaries | **Strong core, sharp Phase-2 edges** | §1.5 split clean; but a real agents↔framework cycle, provider double-resolution, and a live eval-path provenance re-leak |
| 2. Right abstractions | **Mostly right; a few under-built seams** | no agent/validator/stage/provider-dispatch contracts; `getattr`-over-Protocol at key boundaries |
| 3. Error-handling | **Coherent policy** | graceful-vs-hard-fail resolved; the one gap is the jury floor never read mechanically |
| 4. Extensibility seams | **The weakest dimension** | orchestrator, agents, validators, registries, enrichment, providers are all extend-by-modify |
| 5. Maintainability | **Good; cohesive modules** | duplication that will multiply (persistence, fixtures, registry shape, constants sprawl) |
| 6. Traceability | **Under-built for multi-agent** | telemetry computed then dropped; no run-correlation spine; bare spans |
| 7. Up-to-date idioms | **Excellent** | 3.13/Pydantic-2 used well; only minor edges |

---

## 1. Findings by dimension

Within each dimension, ordered by priority. Findings that surface in several layers are stated
once under their primary dimension with cross-pointers. Every `file:line` was opened by a verifier;
paths are the corrected ones (several finders dropped a subpackage segment). Priority reflects the
verifier's adjudication, including its **down**grades.

### 1.1 Coupling & boundaries (highest-value dimension)

**The good news first, because it is load-bearing `[V]`.** The §1.5 invariant holds in code:
`jury_node` routes solely on the typed `verdict.verdict` enum and reads no LLM-authored content as
a control input (`orchestrator.py:683-695`); provenance facts are sourced from the billed cost
ledger, not model self-reports (`cli/extract.py:816-817, 984-1018`, ADR 0065); the cross-subpackage
import graph is *almost* a clean DAG (`schemas/`, `providers/`, `registries/`, `state/` are leaf-ish
and never import upward); and the Provider ABC leaks no vendor type to its consumers (agents see only
capability hints + typed output). A dedicated hunt for new LLM-content-as-provenance edges found
none in the ship path. That is the hard part, and it is done well.

**FIX-BEFORE-PHASE-2**

- **The eval persistence path re-leaks the LLM-self-reported model as run provenance — a live
  recurrence of the ADR-0065 §1.5 leak `[V, re-verified by hand]`.** The persistence choreography is
  *private to* `cli/extract.py` and was re-implemented independently in `eval/runner/runner.py`. The
  CLI path stamps the billed model (`_billed_extractor_model`/`_stamp_billed_model`,
  `cli/extract.py:984, 1000`). The eval path does **not**: `eval/runner/runner.py:388-390` calls
  `handle.update_lineage(model=str(meta.model), …)` where `meta = spec.extraction_metadata` (the
  model's self-report), and the ledger fallback at `:402-404` only fires `if model is None` — already
  False after line 389. So the eval run record's `lineage.model` records what the model *said it was*,
  not what was billed — the exact 0002/ADR-0065 defect, still live in the sibling implementation the
  original fix never touched, despite ADR 0039's stated goal that eval and extract produce comparable
  records via "same code, different pile." Root cause: persistence orchestration was never extracted
  into a shared seam, so the correctness invariant lives in one of two parallel call sites. Phase 2's
  `generate` verb will be a *third* copy. **Impact: correctness, Phase-2-extensibility.** (S33)
- **`spec_version` is LLM-authored, with no current-version constant and no load-time equality gate —
  the missing enforcement seam for the §0.6 no-migration contract `[V, re-verified by hand]`.**
  `spec_version: int = Field(ge=1)` (`attack_spec.py:442`) is floor-checked only; the Extractor emits
  the whole AttackSpec (`output_type=AttackSpec`), so the *model* authors the framework's own
  versioning fact — an unflagged member of the same family as `lineage.model`/`completeness_score`,
  but fixed nowhere. `_load_spec_from_yaml` (`cli/extract.py:406-413`) re-validates an edited spec
  with `AttackSpec.model_validate` and **no version check**, and a repo-wide grep finds **no
  `CURRENT_SPEC_VERSION` constant anywhere** in `cyberlab_gen/`. Yet `docs/schema.md:28,74` promise
  the version is framework-recorded and gate-loaded, and `architecture.md §0.6` makes "refuse to
  *load* old-schema artifacts, never migrate" inviolable. Phase 2's `validate`/`fix` verbs load
  existing labs from disk — that is precisely where an old-schema spec must be refused, and today
  there is neither a framework writer that stamps the version nor a gate that refuses a mismatch.
  Latent today (Phase 1 only produces fresh specs); a contract hole the moment a load-from-disk verb
  ships. **Impact: correctness, Phase-2-extensibility.** (completeness-critic; hand-verified)
- **The jury rubric-floor score channel is built end-to-end but the framework decision never reads
  it `[V]` — the single open must-fix from 0003 §4-A, re-confirmed.** `JuryVerdict.scores` carries
  `min_dimension()`/`all_above(floor)` (`agents/extractor_jury/schema.py:41-60`) and
  `DEFAULT_RUBRIC_FLOOR = 0.7` is a constructor param — but the floor reaches only the *prompt*
  (`jury.py:137`), `jury_node` routes solely on the verdict enum (`orchestrator.py:683-695`), and
  `all_above`/`min_dimension` have **zero call sites** (dead code). A verdict of `approve` with every
  dimension at 0.1 ships at high confidence. This is a *coupling* defect (a fully-typed cross-stage
  channel with no framework consumer) as much as an error-handling one (§1.3); ADR 0021 pt 5 still
  *falsely vouches* a `model_validator` enforces it (it checks only feedback-item counts,
  `schema.py:93-104`). The Phase-2 Planner-Jury and Critic reuse this verdict+scores shape, so the
  pattern that lands here propagates. **Impact: correctness, Phase-2-extensibility.** (S4/S10/S43;
  see also S39, §1.3)
- **The Provider "seam" resolves capability→model *twice* with two algorithms that can diverge
  `[V]`.** `ProviderRegistry.resolve()` returns the first entry whose provider is *configured*
  (`providers/ranking.py:152-161`); `AnthropicProvider._resolve_model()` independently re-walks the
  *same* rankings file and returns the first *anthropic* entry, ignoring configured-ness
  (`anthropic_provider.py:312-326`). The capability is resolved once for pricing/prompt-overlay and
  again inside the adapter for the actual call; they agree today only because anthropic is the sole
  configured provider and first in every list. The moment a second provider is configured (Phase 2),
  the registry could resolve OpenAI while the adapter still picks Opus — the billed/reported model
  would not match the resolved/priced one. Two sources of truth for the unit of pricing and reporting
  is a latent mis-billing trap the linter cannot see. **Impact: correctness, Phase-2-extensibility.**
  (S19; see also S18/S46 in §1.4)

**SHOULD-FIX**

| Finding | Evidence (`file:line`) | Why it bites Phase 2 | V/I |
|---|---|---|---|
| `agents↔framework` is a genuine import cycle, dissolved by one lazy import | `orchestrator.py:52` runtime-imports `ExtractionResult` from agents; `extractor.py:211-215` lazy-imports `framework.refinement` to avoid a load-time cycle | Each of the 6+ new agents re-hits this; a stray top-level framework import becomes a process-start `ImportError`. Move shared result/patch contracts to a leaf module. (verifier **lowered** to should-fix) | V |
| Agents erase `MergedRegistries` to bare `object` + runtime `isinstance` | `extractor.py:123`, `jury.py:63` type `registries: object`, narrowed at `extractor.py:152,218,345`, `jury.py:101` | The cited cycle **does not exist on that edge** (`registries/merge.py` imports neither agents nor framework; `StaticSchemaValidator` types it properly). Pyright is blinded at four agent/registry seams Phase 2 multiplies — type it `MergedRegistries`, drop the guards. | V |
| Provider double-resolution + discarded `agent_label` | `call_surface.py:90-106`, `anthropic_provider.py:312-326`; `complete*` `del agent_label` at `anthropic_provider.py:154,177` | Resolve `(provider,model)` once; pass the concrete id down so adapters never re-read rankings. (same root as the FIX-BEFORE provider item) | V |
| `NvdClient` Protocol lives in `framework.enrichment` but agents+validators depend on it | defined `framework/enrichment.py:139`; imported by `extractor.py:57`, `tools.py:53`, `jury.py:40`, `grounding_validator.py:61` | A port consumed by three packages is a framework back-edge; Phase-2 MITRE/OSV/KEV ports would accrete the same way. Move to a neutral ports module. | V |
| CLI reaches into the orchestrator's private internals | `cli/extract.py:241` imports `_ingestion_summary` with `# pyright: ignore[reportPrivateUsage]`; `_state_to_run_result` (`:296-340`) re-derives the `HALTED_*` mapping `_finalize` owns (`orchestrator.py:863-891`) | The CLI drives `build_pipeline` (not `run_pipeline`) to read raw `PipelineState`; two copies of "terminal state → result/halt" must stay in sync as Phase-2 statuses multiply. Widen `PipelineOutcome` to carry proposals + cost basis. | V |
| The jury holds `propose_*` tools and is told *in prose* not to use them | `jury.py:105,113-120` wire the full `ExtractorToolExecutor` + `extractor_tool_definitions`; the prohibition is `extractor_jury/prompt.md:32-35` | §1.5 says the split is "enforced by tool availability." Containment here is by *not-reading* downstream, not *not-offering*; Phase-2 reviewers (Planner-Jury, Critic) copy it. Split a verify-only tool set. *No live leak (proposals are discarded), but the pattern propagates.* | V |
| Finding locators use string-id list indices the patch parser rejects | enrichment/static/grounding emit `…cves[{cve_id}]`, `chain_steps[{step.id}]` (`enrichment.py:432,510`; `static_schema_validator.py:323,348`; `grounding_validator.py:207`); `refinement._parse_path` raises on any non-integer index (`refinement.py:127-132`) | Latent only because refinement is fed *solely* by jury feedback today. The first time a validator/enrichment finding feeds a targeted patch (a natural Phase-2 step), it raises instead of addressing. Canonicalize on integer indices at the producer. (S15 — also error-handling/correctness) | V |

**NICE-TO-HAVE:** `AdvisoryReference.source`/`completeness_score` mistypings (covered in §1.5 where
they primarily sit). The taxonomy clean-separation (catalog vs runtime-registry vs external-source)
is a **positive** to preserve — see §4.

### 1.2 Right abstractions / design patterns

The patterns that exist are well-chosen and not over-engineered (the `CostRecordingProvider`
decorator-wrap, the `ExtractRunner` Protocol seam, the `ENTRY_KEY_FIELD` ClassVar dispatch that
beats the doc's `getattr`-fallback chain). The findings are all *under*-built seams at Phase-2
plug-in points — a pattern hand-rolled where a clean one belongs, or no seam where one is needed.

**FIX-BEFORE-PHASE-2**

- **No reusable agent contract above `AgentRunner` `[V]`.** `AgentRunner` (`call_surface.py:60-183`)
  cleanly owns the *call mechanics* (capability dispatch, structural-retry, tool loop) but stops
  below the agent-orchestration level. `Extractor.extract` (`extractor.py:141-180`) and
  `ExtractorJury.review` (`jury.py:85-121`) each hand-roll the *identical* six-step sequence — guard
  `isinstance(registries, MergedRegistries)`, derive `source_ids`, build the executor, build
  messages, `run_with_tools`, unpack — and `Extractor.refine` re-implements a second bounded loop by
  hand. Phase 2 replicates this 6+ times (Planner, four Generators, Critic, Repair); every one will
  copy this preamble (and the `registries: object` workaround), and the invariants being copied are
  exactly the §1.5 ones that must not drift. **Its twin, surfaced by the completeness pass:** there is
  no **validator-layer** contract either — `StaticSchemaFinding/Result` and `GroundingFinding/Result`
  are independent `InternalModel`s with divergent `validate()` signatures
  (`validators/static_schema_validator.py:109-160` vs `grounding_validator.py:84-133`), so each
  Phase-2 mechanical layer (L2/L3/L5) is a bespoke type-pair *plus* bespoke orchestrator node surgery.
  A shared `Finding`/`Result` base would also let the locator convention (§1.1) be enforced once.
  These two missing contracts are the single highest-leverage refactor for Phase-2 cleanliness.
  **Impact: maintainability, Phase-2-extensibility.** (S7 + completeness)

**SHOULD-FIX**

| Finding | Evidence | Why | V/I |
|---|---|---|---|
| Routing logic is smeared across node bodies, each re-implementing the no-progress-bail / global-cap / budget-bump dance | `validate_node`/`grounding_node`/`jury_node` repeat it; signature helpers duplicated (`orchestrator.py:567-576` vs `630-639`, `894-911`) | The retry-loop control structure is one concept implemented three times by copy-edit; a Phase-2 Planner/Critic loop is a fourth copy. Extract one `route_with_budget(...)` primitive. | V |
| `ExtractRunner` Protocol omits the stateful read-back surface | Protocol declares only `run`/`re_run_with_feedback` (`cli/extract.py:132-146`); persistence reaches `last_state`/`content_hash` via `getattr(...)`-with-None and an `isinstance(PipelineExtractRunner)` narrowing (`extract.py:977,1032,747`) | `getattr`-with-default is the canonical "leaking abstraction" signal; a rename silently degrades every persistence path to "no partial spec saved" with no type error. Promote to the typed contract. | V |
| `OverlayRegistryFile._entry_key` reads `ENTRY_KEY_FIELD` via `getattr` on a `BaseModel`-bounded generic | `schemas/registries.py:356, 389-402` | A Phase-2 entry type that forgets the ClassVar fails at runtime, not under pyright (bound is `BaseModel` per ADR 0004 case 2). A `KeyedRegistryEntry` Protocol bound restores the static guarantee. | V |
| Two parallel status taxonomies with same-named members, different string values | `PipelineStatus.SHIPPED_LOW_CONFIDENCE="shipped_low_jury_confidence"` (`orchestrator.py:233`) vs `RunStatus.SHIPPED_LOW_CONFIDENCE="shipped_low_confidence"` (`run_store.py:88`); bridged by a lossy bool + re-derivation in three places (`extract.py:296,1061,1076`; eval `runner.py:469`) | Three status vocabularies hand-mapped with subtle drift (eval's mapper lacks `INTERRUPTED`/`CRASHED`). Consolidate into one shared mapping. (nice-to-have boundary) | V |
| Anthropic request config + the single `TokenUsage.cache_write_tokens` field bake one vendor's model into the shared cost layer | `anthropic_provider.py:284-310`; `cost_ledger.py:25-29,143` (5-min rate as a Phase-0 stopgap) | Keep cache flags in the adapter (correct), but the single cache-write field is in the *shared* layer and will need split tiers when a second provider bills cache differently. | V |

### 1.3 Error-handling philosophy

**The brief's explicit question — is the graceful-vs-hard-fail asymmetry from 0001 symptomatic of a
broader inconsistency? — answers no `[V]`.** Within the enrichment/validation layer the asymmetry is
*resolved*: NVD ("no client" → honest skip) and MITRE (uncatalogued-but-well-formed → unverified
pass-through) now degrade identically and symmetrically. The error taxonomy is a coherent, rooted
hierarchy applied as a stated *policy*, not per-site: `EmitTruncated` subclasses `MalformedOutput`
but is never retried; `BudgetExceeded` subclasses `HardFailure` so eval treats it as global-fatal;
`AgentFailure` deliberately is *not* a `ProviderError`; the CLI catch order is narrow-to-broad
(`cli/main.py:328-339`); persistence writes are best-effort specifically so they never mask the
propagating error (`run_store.py:233-240`). **This is a genuine strength** (S38) — recorded as a
positive in §4. The dimension's findings are two:

**FIX-BEFORE-PHASE-2**

- **The one numeric quality gate on the only shipping artifact is advisory `[V]`.** This is the jury
  rubric-floor item (§1.1), here under its error-handling face: the docs + ADR 0021 promise a
  mechanical floor; the framework never enforces it; the helper that would (`all_above`) is dead
  code; the model can self-contradict (low scores + `approve`) and nothing catches it. (S4/S10/S43)
- **No test pins the jury floor — the suite *masks* the open must-fix `[V]`.** Every jury fixture
  pairs its verdict with high scores (`pipeline_fakes.make_verdict` hardcodes 0.9,
  `pipeline_fakes.py:122-134`; `test_extractor_jury.py:123-246` uses 0.9/0.65/0.2 only to satisfy the
  verdict↔feedback validator), so an `approve`-with-all-0.1 verdict would ship and **no test fails**.
  CLAUDE.md's own discipline ("every claimed behaviour gets a test that fails when it breaks") is
  violated at exactly the gate the docs claim is enforced. When the floor is wired, the guarding test
  must be written alongside. **Impact: correctness, Phase-2-extensibility.** (S39)

**SHOULD-FIX**

- **No mechanical dedup of proposals against the merged registry before acceptance `[V]`.** The
  acceptance path (`auto_accept_to_overlay → accept_* → write_overlay_entry`) never asks
  `MergedRegistries` "does this name already exist in bundled+overlay?"; `write_overlay_entry`
  (`overlay_writer.py:61-62`) dedups only *within* the overlay file, so a proposal colliding with a
  **bundled** entry is written and silently shadows it at merge time (overlay-wins, `merge.py:70-74`).
  The only novelty guard is the prompt-level `build_registry_digest` (`extractor.py:333-362`) — an
  LLM-discretion soft lever for what is a purely mechanical framework check. In Phase 2, with a second
  proposer (Planner) and many more registries, accidental shadowing of a curated bundled entry by an
  auto-accepted overlay entry becomes materially more likely. Make "already registered" a mechanical
  check at accept time. **Impact: correctness, Phase-2-extensibility.** (S25)

### 1.4 Extensibility seams — the weakest dimension

Every Phase-2 plug-in point is currently open-for-modification rather than open-for-extension. None
is a Phase-1 defect; together they are the difference between Phase 2 being "add a module" and
"edit core wiring," and they are the most important thing in this report.

**FIX-BEFORE-PHASE-2**

- **`build_pipeline` is a ~390-line closure factory that *is* the state machine; every Phase-2 stage
  is surgery on it `[V]`.** `orchestrator.py:418-811` defines 10 nested closures over an
  ever-growing kwarg list, a hand-maintained `_Node` StrEnum (`:365-371`), and a hand-wired
  `add_node`/`add_conditional_edges` block where each conditional edge repeats its destination-map
  literal (`:752-787`). Adding the Planner means: a new enum member, a new node closure inside this
  already-long body, a new kwarg, and hand-edited edge maps — with no compile-time check that the
  enum, the registration, and the maps stay in sync (a missing destination key is a runtime
  `KeyError`, not a type error). **Two structural companions:** the routing duplication (§1.2), and —
  surfaced by the completeness pass — `PipelineState` is a single linear in-place-mutated channel
  with **no LangGraph reducers** (`orchestrator.py:238-289`), so Phase 2's parallel generators
  (the natural fan-out) have no merge mechanism and would last-write-win or error. Introduce a
  Stage/Node abstraction (a registered `{name, work_fn, routing_fn}` the builder iterates, deriving
  edge maps from a typed enum) and Annotated-reducer channels before the first parallel node lands.
  **Impact: maintainability, Phase-2-extensibility.** (S1 + S2 + completeness)
- **External-source enrichment hardcodes NVD/MITRE branches, bypassing the data-driven adapter
  contract the registry already declares `[V]`.** `enrich()` (`framework/enrichment.py:742-779`)
  dispatches by hardcoded constants (`_NVD_SOURCE_ID`, `_MITRE_SOURCE_ID`) into bespoke
  `_enrich_cves`/`_enrich_techniques`/`_parse_nvd_response`, and loops the *rest* only to emit stub
  skips. Meanwhile the registry models the full contract that is never consulted:
  `EnrichmentTrigger{field, action, endpoint}` (`schemas/registries.py:92-101`),
  `ExternalSourceEndpoint{…, response_schema_ref}` resolved "by the adapter module under
  `cyberlab_gen/external_data_sources/<id>/`" (`registries.py:59-75`). Phase 2 explicitly adds
  OSV/MSRC/KEV/EPSS adapters — the very sources currently routed to stub-skips — and each one, as
  built, means a new branch + a new `_enrich_<source>` + a new `_parse_<source>_response` + a new
  injected client field: modify-core per source. Drive enrichment from the declared
  `enrichment_triggers` and resolve a per-source adapter, with NVD as the first adapter behind the
  seam. **Impact: Phase-2-extensibility, maintainability.** (S13)
- **The Provider ABC is a one-vendor seam `[V]`.** `ProviderRegistry.resolve()` returns a
  `(provider, model)` entry, but **no consumer dispatches on `provider`** — the CLI constructs one
  `CostRecordingProvider(AnthropicProvider())` and hands that single instance to every agent
  (`cli/main.py:256-262`); the registry's provider dimension is decorative (no
  provider-name→instance map exists). `model_rankings.yaml` already lists OpenAI placeholder rows, so
  multi-provider is intended, but the wiring to honour a non-anthropic resolution does not exist —
  adding OpenAI is a modify-core change (rewire `main.py`, invent dispatch) plus the double-resolution
  correctness trap (§1.1). Introduce a provider factory/dispatch keyed on the resolved name.
  **Impact: Phase-2-extensibility, maintainability.** (S18 + S19/S46)

**SHOULD-FIX**

| Finding | Evidence | Why | V/I |
|---|---|---|---|
| The seven-registry shape is hand-replicated across ~10 sites | `loader.py:58-93,253-283`; `merge.py:90-193`; smoke test `test_registry_load.py:101-109` | "Add a registry" = ~10 coordinated edits across two modules with no compiler help (a forgotten index line yields a silently-empty index). A `RegistryDescriptor` table iterated by load/merge makes it one row. Phase 2 adds many registries. | V |
| Proposal acceptance is parallel-by-hand per type | three near-identical `accept_*` (`proposal_acceptance.py:90-126`), three copy-pasted loops (`:129-168`), mirrored in the CLI (`extract.py:508-553`) | The Planner is a documented proposer (§5.20) → a fourth branch at every site. A `Proposal` protocol collapses it to one generic accept over a flat list. | V |
| Proposal-authority machinery is hardcoded to `"extractor"` | `proposed_by='extractor'` literals (`proposals.py:67,96,118`); `EXTRACTOR_FACET_CATEGORIES` frozenset gate (`proposals.py:32`, `tools.py:347`); `ProposedFacet.category: Literal["target","lab_class_signal"]` (`:80`) | The Planner's runtime facets are *structurally rejected* at construction. Stamp `proposed_by` at the framework accept boundary and make the authority gate a per-agent input. | V |
| No streaming/sectioned-emit seam in the locked call surface | both ABC methods return a fully-materialized `ProviderResponse` (`base.py:209-247`); `EmitTruncated` halts atomic emits (`anthropic_provider.py:255-266`) | ADR 0032/0033's durable truncation fix is streaming + sectioned emit — inexpressible by a single terminal-response method; it will break the "locked" surface and ripple through the decorator + retry layers. Decide the contract before the emit work. | V |
| `AttackSpec`/`LabManifest` share no envelope base | `AttackSpec` hand-rolls `spec_version`/`spec_kind`/`source` + `_scope_consistency` (`attack_spec.py:432-503`); `SpecKind` already enumerates both; no `LabManifest` or shared base exists | Phase 2's manifest re-declares the envelope from scratch; the no-migration loader needs a `spec_kind` discriminator to dispatch on. Extract a thin `SpecEnvelope` base now, with one concrete subclass to refactor against. (verifier **lowered** to should-fix) | V |
| Instrumentation is wired per-node inside `build_pipeline` | stage-span wrapping is manual per fixed node (`orchestrator.py:757-761`); `CostRecordingProvider` hand-wired once (`cli/main.py:257-262`) | The provider seam covers new agents for free (good), but each new node must be hand-wrapped to be traced — observability that depends on every author remembering develops holes. Lift to a node-registration wrapper. | V |
| In-process `MergedRegistries` snapshot goes stale after an overlay write | loaded once at the composition root (`cli/main.py:263`); overlay written mid-run (`extract.py:543-553`); nothing re-reads | Today only the Extractor proposes and the write is after-ship, so it never bites. In Phase 2 the Planner (same process) reads the captured-once object and won't see just-accepted entries — re-flagging them unknown. (completeness) | V |

### 1.5 Maintainability

Modules are cohesive and read well for a newcomer — notably `cli/extract.py` at 1117 lines is
*honest surface area* (small single-purpose helpers, injected seams), not a God-function, and
`enrichment.py` at 791 lines is one public entrypoint with a tight helper cluster. The findings are
about *duplication that will multiply* and a few naming traps.

**SHOULD-FIX**

| Finding | Evidence | Why | V/I |
|---|---|---|---|
| Persistence orchestration is duplicated CLI↔eval and the duplicate diverges on a correctness invariant | the §1.1 eval provenance re-leak (S33) | The maintainability root of the FIX-BEFORE provenance bug: extract a shared persistence service so the model-provenance rule lives in one place and Phase-2 `generate` plugs in instead of re-cloning ~120 lines. | V |
| Checkpoint serializer allowlist is a hand-maintained string-tuple with a silent-block failure mode | `_REGISTERED_CHECKPOINT_TYPES` (`checkpointing.py:48-60`); the docstring states the trap; AttackSpec rides `pickle_fallback=True` (`:80`) | Every new persisted `PipelineState` channel type (Phase 2: LabPlan, IaC, Critic reports) must be added by hand or resume silently breaks, and inherits the pickle path with no picklability guard (0003 §3-A). Derive from `model_fields` or add a full-state round-trip smoke test. (run-1 finding, verified) | V |
| `AttackSpec` construction fixtures duplicated across ~9 test files despite a shared `make_spec` | `pipeline_fakes.make_spec` (`pipeline_fakes.py:64`) imported by only 4; ~6 hand-roll their own (`test_attack_spec.py:140`, `test_extractor.py:138`, `test_extractor_jury.py:84`, `test_grounding_validator.py:60`, `test_refinement.py:71`, `test_enrichment.py:167`, `test_static_schema_validator.py:106`, `test_cli_extract.py:95`) | Phase 2 extends the spec heavily; a new required field is a ~9-file shotgun edit, and "the canonical valid spec" is not single-sourced. Consolidate behind one parametrizable builder. | V |
| Duplicated cross-layer primitives across enrichment/validators | two `_collect_technique_refs` with divergent shapes (`enrichment.py:656` vs `grounding_validator.py:318`); `_NVD_SOURCE_ID` redefined (`enrichment.py:84`, `grounding_validator.py:66`); MITRE id a bare literal in one file, a registry lookup in another | The grounding validator's own docstring boasts it relocated duplicated checks — yet these are re-duplicated. Phase 2's L2/L3/L5 will need the same helpers; without a shared home the divergence count grows linearly. | V |
| MockProvider has low fidelity on every failure/tool path | deletes `tools`/`tool_executor`/`max_iterations` and dispatches as `complete` (`mock_provider.py:192`); no tool loop, no truncation, no typed `ProviderError`; reconstructs conversation without intermediate tool turns (`:252`) | Phase-2 agents are tool-heavy and tested mostly against this mock; their error/loop branches go untested at the unit level, and conversation-shape assertions pass against a shape the real adapter never emits. Keep the mock dumb but add typed doubles per failure mode (the suite already does this well — §4). | V |
| No central configuration surface; tunables scattered across ~9 modules | `DEFAULT_*` in `call_surface.py:47`, `extractor.py:70/73/94`, `jury.py:49/52`, `orchestrator.py:86`, `enrichment.py:83`, `ingestion.py:54`, `anthropic_provider.py:100`; ad-hoc env reads; no `config.py`/`BaseSettings` | Phase 2 adds 6+ agents each with its own budgets; with the current pattern each lands as a fresh module constant with no override surface. The two-`DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` collision (next row) is the first symptom. (completeness) | V |

**NICE-TO-HAVE**

| Finding | Evidence | V/I |
|---|---|---|
| Two `DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` with the same name, different defaults (2 additional vs 3 total) one layer apart; compose multiplicatively | `agents/call_surface.py:47` vs `orchestrator.py:86` | V |
| `AdvisoryReference.source` typed `ExternalDataSourceId` but documented as a free-form publisher label | `attack_spec.py:273`; the validator comment says it "can never resolve" (`static_schema_validator.py:294-295`) — the type asserts a contract the design denies (0003 cat-2) | V |
| `completeness_score` is a bare LLM-authored float in a block named "run metadata," no provenance, no framework writer/consumer | `attack_spec.py:410` — the last unfixed member of the §1.5 content-as-provenance family (0003 §4-B); contract-safe only because nothing reads it yet. Wrap as `Provenance[float]` or rename to mark the self-report. | V |
| Two distinct classes both named `DetectionFormatEntry`, forcing an aliased re-export | `attack_spec.py:116` vs `catalogs.py:71`; `schemas/__init__.py:39-41` | V |
| `run_extract` accepts an `interactive` param it immediately `del`s; mode mutual-exclusion validated in two places | `extract.py:689-690,716-717`; `main.py:218-222,299-303` | V |
| `MergedRegistries` accessor surface asymmetric (`lab_credentials` has no index); `static_catalogs` loadable two non-equivalent ways | `merge.py:113-146`; `loader.py:173-182` vs `merge.py:169-173` | V |

### 1.6 Traceability & observability — under-built for a multi-agent run

The data-*capture* level is solid (the run-store's persist-on-every-exit-path, the per-attempt cost
ledger, the per-request truncation-decomposition message). The gap is *reconstruction*: the
instrumentation is Extractor-shaped, and the three pillars — logs, spans, cost-ledger — share **no
correlation key**. A senior engineer could debug the single-agent Phase-1 loop from the cost log;
they could not reconstruct a failed Phase-2 multi-agent run from what is captured.

**FIX-BEFORE-PHASE-2**

- **Orchestrator routing/iteration telemetry is computed, then dropped on persistence `[V]`.**
  `PipelineState` carries `structural_attempts`/`grounding_attempts`/`refinement_iterations`/
  `total_iterations`/`verdict_history` and `_finalize` copies them onto `PipelineOutcome`
  (`orchestrator.py:262-289, 881-890`); `RunRecord` has a `metrics` field and `finalize()` accepts
  one (`run_store.py:134, 205-227`) — but both the extract and eval persistence paths call `finalize`
  with **status + reason only** (`cli/extract.py:980-981`; `eval/runner/runner.py:408`), and
  `RunResult` doesn't even carry `refinement_iterations`/`verdict_history`. `run.json` records *what
  files exist* and *how it ended*, never *how it got there*. For a Phase-2 run threading
  Planner→Generators→Critic with per-agent refinement, the loop trajectory is the first thing you
  need and it is exactly what's discarded. Thread the counters + verdict history into the persisted
  record. **Impact: maintainability, Phase-2-extensibility.** (S49)

**SHOULD-FIX**

| Finding | Evidence | Why | V/I |
|---|---|---|---|
| The per-run log is declared + ADR-documented but never written into the run dir; no `run_id` correlation | `RUN_LOG_FILENAME="run.log"` declared/exported but never written (`run_store.py:65,334`); the real log is `run-<stamp>[-run_id].log` in a platform dir (`logging_setup.py:93-95`); `setup_logging` called with no `run_id` (`main.py:161`) before the run dir exists | Debugging means timestamp-correlating two unrelated directories; `run.json` has no field naming its log. The run-store's whole premise ("never spend money and end with nothing to read") is undercut for the richest artifact. Thread the `run_id`, write `run.log`, record its path. (S35/S50; cf. 0003 §3-C) | V |
| Phoenix stage spans are bare name-only spans; framework decisions are untraced | `stage_span` sets no attributes (`tracing_setup.py:139-151`); nodes wrapped with just the static name (`orchestrator.py:376-397`) — the entire package has zero `set_attribute`/`set_status` calls | The native instrumentor captures the LLM half richly; the *framework* half (route, iteration, verdict, `run_id`) is absent — blind to exactly the deterministic decisions §1.5 exists to make auditable. Phase 2 multiplies the looping. Additive: have `stage_span` accept attributes. | V |
| Cost-ledger records per-attempt `outcome`+`capability` to enable retry-rate/wasted-spend analysis, then never rolls them up; entries carry no run/iteration/stage context | `CostLedgerEntry` (`cost_ledger.py:163-178`); `CallOutcome` docstring states the intent (`:150-157`); `CostReportBlock` exposes only by_agent/model/provider (`:181-188`); `purpose` is a per-instance constant (`cost_recording_provider.py:74`), `run_id` hardwired `"cli-session"` (`main.py:175`) | "Which agent's refinement loop burned budget on FAILED attempts" is a first-order Phase-2 question whose answer sits unused in the ledger. Add `by_outcome`/`wasted_usd`; thread the real `run_id`. (S52 + run-1 cost-context finding) | V |
| Honest-skip / non-material enrichment records are computed + persisted but never surfaced in the run report | `EnrichmentResult.skipped`/`non_material_field_paths` populated (`enrichment.py:296-302`) but the report renders only material discrepancies (`extract.py:666-680`) | The module's own docstring promises skips are "surfaced so the gap is honest." In Phase 1 *every* lookup is a skip (no NVD client) and the user is told nothing; as adapters land, this is the primary "we couldn't confirm" signal. (S14) | V |

**NICE-TO-HAVE:** the pricing-vs-ranking smoke test is one-directional (rankings→pricing only;
`test_pricing_coverage.py:22-34`) — it won't catch a billed model that drifts out of `pricing.yaml`
(S41). The checkpoint resume reads the globally-newest thread across the whole DB (positional, not
addressed by the run's own thread; `checkpointing.py:116-136`) — correct for one-run-per-file, a
foot-gun once `--resume` or a shared store lands (S6).

### 1.7 Up-to-date idioms — the strongest dimension

Largely a clean bill, reported honestly: PEP-695 generics (`Provenance[T]`,
`OverlayRegistryFile[E: BaseModel]`), `StrEnum`, built-in generics, `Annotated`/`StringConstraints`
primitives, and `field_validator`/`model_validator` are all idiomatic 3.13/Pydantic-2; the
`Provenance[T].__reduce__` pickle fix (ADR 0066) is a sophisticated, correct, regression-tested
work-around for a precise pydantic generic-registration corner; and the typed-contents-not-stringified
rule is honored (the one `JsonValue` escape hatch, `FieldPatch.new_value`, is re-validated through
`model_validate`, not a stringified-JSON smell). Two minor edges:

**NICE-TO-HAVE**

- The generic provenance-structure walk descends models but **not** `Provenance.value`
  (`grounding_validator.py:293-315`), and traverses models via `value.__dict__` rather than
  `model_fields` `[V]`. Fine for Phase-1 scalar/`list[str]` values; a silent blind spot the moment a
  Phase-2 artifact wraps a nested model in `Provenance[SomeModel]` (likely for manifests/detection
  rules), and `__dict__` couples the walk to Pydantic internal storage. Document the scope decision
  and switch to `model_fields`. (S17)

### 1.8 What the completeness pass added

After the per-layer fan-out, a critic re-read the survivor set against four cross-cutting dimensions
the ten layers had no single home for, and confirmed each against source (not inferred): **(1)** the
LLM-authored `spec_version` / no-migration gap (promoted to FIX-BEFORE, §1.1); **(2)** the absent
central configuration surface (§1.5); **(3)** the missing validator-layer contract (folded into the
agent-contract item, §1.4); **(4)** `PipelineState`'s lack of LangGraph reducers for fan-out (folded
into `build_pipeline`, §1.4); plus the in-process registry-staleness seam (§1.4) and a
sync-over-async double-`asyncio.run` foot-gun (`cli/extract.py:282` + `checkpointing.py:106`, whose
broad `except` would silently drop a partial spec if a Phase-2 async finalize ever re-enters the loop
— nice-to-have, but real).

---

## 2. The prioritized split

### FIX-BEFORE-PHASE-2

*Correctness / a false guarantee a Phase-2 reader inherits:*
1. **Jury rubric floor is never read mechanically** (and no test pins it) — the single open must-fix
   from 0003 §4-A, re-confirmed; correct ADR 0021 pt 5. (§1.1/§1.3)
2. **Eval persistence re-leaks the LLM-self-reported model as provenance** — live §1.5 recurrence of
   the ADR-0065 leak in the un-shared duplicate. (§1.1)
3. **`spec_version` is LLM-authored with no current-version constant and no load-time gate** — the
   missing §0.6 no-migration enforcement seam. (§1.1)
4. **Provider capability→model is resolved twice by two algorithms that can diverge** — a latent
   mis-billing trap. (§1.1/§1.4)

*Open/closed seams Phase 2 provably hits hard (extend-by-modify today):*
5. **No reusable agent contract above `AgentRunner`, and no validator-layer contract** — the
   most-replicated Phase-2 plug-in (6 agents, 3 validators). (§1.2/§1.4)
6. **`build_pipeline` is the state machine** — new stages are surgery; routing is copy-pasted across
   nodes; `PipelineState` has no reducers for fan-out. (§1.4)
7. **External-source enrichment hardcodes NVD/MITRE, bypassing the data-driven adapter seam** — the
   NVD/MITRE/OSV adapter point. (§1.4)
8. **The Provider ABC is a one-vendor seam** (no provider dispatch) — the OpenAI add-point. (§1.4)

*Control-flow / latent-correctness:*
9. **The everyday-budget predictive interrupt cannot fire inside the orchestrator loop** — only
   between whole CLI re-runs; already ADR 0063 (PLANNED), to land *before* Phase-2 multi-stage loops
   10× the per-iteration cost. (§1.1)
10. **Finding locators use string-id indices the patch parser rejects** — latent until a non-jury
    finding feeds refinement; canonicalize at the producer. (§1.1)
11. **Orchestrator routing/iteration telemetry is dropped on persistence** — a failed multi-agent run
    can't be reconstructed. (§1.6)

### SHOULD-FIX

Persistence-service extraction (DRY the §1.1 leak); the `agents↔framework` cycle + the `object`
type-erasure at agent/registry boundaries; the `NvdClient` back-edge; the CLI↔orchestrator
private-internal coupling; jury-holds-`propose_*`-tools (verify-only tool split); the seven-registry
hand-replication and the proposal-acceptance/authority duplication; the streaming/sectioned-emit
seam; the `SpecEnvelope` base; checkpoint-allowlist auto-derivation; the no-mechanical-proposal-dedup
gap; the run.log/run-correlation spine + bare spans + cost-ledger rollups + enrichment-skip
surfacing; central config surface; fixture/primitive duplication; MockProvider fidelity; the
`ExtractRunner`/`ENTRY_KEY_FIELD` typed-contract tightenings; the status-taxonomy consolidation; the
registry-staleness seam.

### NICE-TO-HAVE

`AdvisoryReference.source`/`completeness_score` mistypings; the `DetectionFormatEntry` name
collision; the dead `interactive` param; the two `DEFAULT_STRUCTURAL_RETRY_ATTEMPTS`; the
`MergedRegistries` accessor asymmetry + dual `static_catalogs` load; the one-directional pricing
smoke test; the positional checkpoint-resume read; the provenance-walk `value`/`__dict__` edges; the
sync-over-async foot-gun; the `TokenUsage` single cache-write field.

---

## 3. Overall verdict + the top 5

**Is this codebase at the 20-year-senior-engineer bar for design quality? Yes — with the honest
caveat that "design quality" here is graded against a system that has built one pipeline and must
now build six more agents, four more validators, several adapters, and a fan-out.** On that axis it
is **strongest at the contract core** (the §1.5 split, the schema layer, the cost subsystem, the
refinement convergence, the test discipline) and **weakest at the extension mechanism** (every
Phase-2 plug-in is currently a modification of core code) and at **multi-agent observability** (rich
telemetry computed then discarded). It is not weak anywhere in a way that threatens Phase-1
correctness; the fix-before list is dominated by *forward-looking* seams plus three genuine
correctness items in the §1.5 family the build has otherwise been systematically closing.

**The top 5 to address before building Phase 2 on this:**

1. **Close the three live §1.5 correctness items as one batch** — wire the jury floor (+ its guarding
   test, + correct ADR 0021 pt 5), extract the persistence choreography into a shared service so the
   eval path stops re-leaking the billed-model provenance, and framework-stamp + load-gate
   `spec_version`. They are the same family; fixing them together also kills the duplication root
   (#3 below).
2. **Introduce the missing contracts before the counts multiply** — an agent contract above
   `AgentRunner`, a validator-layer `Finding`/`Result` + layer contract, and a stage-registration
   mechanism (with reducer channels) in the orchestrator. This is the single highest-leverage
   refactor for whether Phase 2 is "add a module" or "edit core wiring," and it is cheapest now, with
   two known shapes to factor against and zero duplication yet to fight.
3. **Make the two adapter seams data-driven/dispatched** — drive enrichment from the registry's
   declared `enrichment_triggers` (NVD as the first adapter behind it) and add a provider
   factory/dispatch keyed on the resolved name (resolving `(provider, model)` exactly once). These
   are the NVD/MITRE/OSV and OpenAI plug-in points, currently modify-core.
4. **Land ADR 0063 (in-loop budget) and a run-correlation spine** — thread the ledger into the
   orchestrator so the predictive interrupt can bound an in-flight loop, and give logs/spans/ledger a
   shared `run_id` while persisting the loop trajectory, so a multi-agent run is both *bounded* and
   *reconstructible*.
5. **Canonicalize the finding-locator convention and split the jury's tool inventory** — one integer-
   index locator enforced at every producer (before validator/enrichment findings feed refinement),
   and a verify-only tool set so the "juries never propose" invariant is enforced by tool availability
   (§1.5) rather than by a prompt sentence the Planner-Jury/Critic would inherit.

Do these five and Phase 2 is a clean build on a strong base. Skip them and the same code remains
correct — but each new agent, validator, and adapter pays a surgery tax, and two §1.5 provenance
leaks plus a false quality-gate guarantee carry forward into the artifacts Phase 2 consumes.

---

## 4. What the review confirmed is genuinely good (honest both ways)

Recorded because a craftsmanship review that only lists problems is dishonest, and because these are
the patterns Phase 2 should *preserve*, not just the ones it should fix:

- **The §1.5 split is enforced structurally, not by convention `[V]`** — agents hold only
  content/judgment tools; routing/budget/stop/ship live in framework nodes; provenance is
  ledger-sourced. No new false-provenance edge in the ship path.
- **The targeted-patch refinement is convergent-by-construction `[V]`** — pure framework,
  dependency-free, unit-testable without spend; unflagged fields stay byte-identical.
- **The cost subsystem is exemplary `[V]`** — Decimal throughout, per-attempt entries, the
  catastrophe ceiling enforced on both success and billed-failure paths (ADR 0047), framework-owns-
  the-cap honored to the letter.
- **The schema layer is the strongest layer `[V]`** — `ArtifactModel` discipline consistent; the
  three reserved `BaseModel` cases correct and documented (ADR 0004); `Provenance[T]` idiomatic and
  the pickle fix correct + regression-tested (ADR 0066).
- **The test suite is senior-grade `[V]`** — asserts behaviour and call-paths, not internal state;
  where the mock can't be faithful it uses a purpose-built typed `Provider` double or the real
  pydantic-ai `FunctionModel` path; the mechanical-consistency smoke tests are genuinely load-bearing.
- **The error taxonomy and graceful-vs-hard-fail policy are coherent `[V]`** — the 0001 asymmetry is
  resolved; subclassing encodes policy; persistence swallows only its own errors.
- **The catalog / runtime-registry / external-source taxonomy is cleanly three concepts, not one
  conflated "registry" `[V]`** — the 0001 MITRE-as-closed-catalog misclassing is resolved at the
  design level, and the LLM/framework split rides on it (the proposal digest can exclude external
  sources *because* the code knows they are tool-adapters).
- **`cli/extract.py` (1117 L) is honest surface area, not a God-function `[V]`** — small named
  helpers, injected seams; reads well for a newcomer.

---

## 5. What the adversarial verification corrected (boundary-sharpening)

The refute-pass did real work; recorded so a future reader does not re-flag refuted items or treat a
downgraded item as a must-fix.

**Refuted and dropped (3):**
- *"GroundingValidator is silently default-constructed, unlike validator/jury/extractor which are
  always injected through narrow Protocols."* **False premise `[V]`:** `build_pipeline` types
  `validator: StaticSchemaValidator` — the *concrete* class, no `_ValidatorLike` Protocol exists. The
  real convention is "agents behind Protocols; validators as concrete classes," so grounding is
  consistent, not anomalous. The design argument collapses.
- *"`to_yaml`/`from_yaml` round-trip relies on un-pinned `HttpUrl`/alias normalization."* **Refuted
  `[V]`:** `test_registries.py:747-758` already isolates the `schema_` alias round-trip, and HttpUrl
  normalization is idempotent and applied at parse time (verified by running the real path), so
  dump→reparse compares two already-normalized models.
- *"`completeness_score` is asserted as truth in an eval metric test."* **Refuted as stated `[V]`:**
  the underlying field residue is real (and tracked in NICE-TO-HAVE / 0003 §4-B), but the test is
  named `test_record_reads_extractor_self_score…` and carries a comment marking the harness metric as
  independent — it does *not* bless the value as authoritative.

**Priority downgrades the verifiers applied** (and this report honors): the `SpecEnvelope` base
(S28) and the `agents↔framework` cycle (S44) were lowered from fix-before to should-fix; the
`completeness_score` agent-layer instance (S12), the streaming-seam urgency, the cost-rollup (S52),
and the per-stage instrumentation (S53) sit at the soft end of should-fix; the two
`DEFAULT_STRUCTURAL_RETRY_ATTEMPTS` collision (S5) and the dead `interactive` param (S36) to
nice-to-have.

---

## Cross-references

- **0003 §4-A** (jury rubric floor — the single must-fix, re-confirmed open here) · **ADR 0021 pt 5**
  (the false `model_validator` claim to correct) · **ADR 0065** (the false-provenance fix the eval
  path diverged from) · **ADR 0039/0053** (run-store persistence — the contract the dropped telemetry
  and unwritten `run.log` under-deliver) · **ADR 0063** (the loop-budget threading work-stream —
  fix-before #9) · **ADR 0055/0058** (external sources as tool adapters — the seam #7 should realize)
  · **ADR 0032/0033** (streaming/sectioned emit — the seam the locked call surface lacks) · **ADR
  0066/0040** (the `Provenance` pickle fix + `pickle_fallback` carry-forward behind the checkpoint
  allowlist finding) · **ADR 0004** (base-class discipline — honored; the `ENTRY_KEY_FIELD` Protocol
  bound would tighten it).
- **Investigations 0001** (graceful-vs-hard-fail — confirmed resolved) and **0002** (the
  model-attribution leak family — fixed in the CLI, **re-found live in eval**, §1.1).
- **`architecture.md §1.5/§1.6/§1.7, §0.5, §0.6`** — the contracts this review judged the code
  against; the deviations are the jury-floor gate (§1.6), the in-loop predictive interrupt (§0.5
  crit 6), and the `spec_version` no-migration enforcement (§0.6).
