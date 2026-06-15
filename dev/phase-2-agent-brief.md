# Phase 2 — Agent task brief

**Purpose.** Decompose Phase 2 of cyberlab-gen ("Planner + Jury") into agent-sized
tasks. By the end of Phase 2, `cyberlab-gen plan <attack-spec.yaml>` reads a validated,
enriched AttackSpec and writes a draft `lab.yaml` (LabManifest skeleton) — phases,
lab_resources, prereqs, inputs, outputs, facets, per-step reproducibility — with **no
code, no IaC, no docs** (those are Phase 3+). Each task is something a single coding
agent can complete in one focused session, with clear inputs, outputs, and exit criteria.
Tasks are sequenced in two waves (a thin vertical slice, then broadening); later tasks
depend on earlier ones.

**Audience.** Coding agents operating in the cyberlab-gen repo, each having read this
brief plus the listed required documents for that task.

**Authority gradient.** When this brief and an architecture document disagree, the
architecture document wins. This brief is the *decomposition* of work the architecture has
already specified; it is not a place to make new architectural decisions. If an agent finds
a real ambiguity, it stops and records the question in `dev/decisions/` rather than guessing
— same discipline as Phases 0 and 1.

**Scope of record.** The single source of truth for Phase 2 done-ness is
`implementation-plan.md §5` (scope §5.1, build inventory §5.2, curated set §5.3,
calibration §5.4, exit criteria §5.5, risks §5.6). This brief decomposes that section; if
§5 evolves, the brief follows it.

**Phase 2 is exactly two agents — but the build inventory is wider.** "Planner + Jury" is
the headline. The full inventory (`implementation-plan.md §5.2`) is: the Planner agent, the
full LabManifest schema, lab-level reproducibility derivation, the Planner-Jury, full
pre-Planner enrichment, Validator Layer 2, the post-Planner interrupt, the extended
refinement coordinator, and the Phase-2 eval additions + curated-set growth. The Generators
(Phase 3), the Critic (Phase 4), and the Repair Agent (Phase 5) are **out of scope** — do not
build them.

**Strategy: depth-first thin vertical slice (locked with the architect).** Wave 1 drives a
single representative blog all the way through Planner → Planner-Jury → Layer 2 to a runnable
`cyberlab-gen plan`, **before** broadening to proposals, full enrichment, the full curated set,
or the interactive interrupt. Wave 1's whole job is to **lock the LabManifest contract against
a real Planner output** — the dominant Phase-2 risk (§5.6). Wave 2 fills out to the exit
criteria. The pipeline graph stays **linear** in Phase 2; the `Stage`/`Node` abstraction and
reducer channels (`dev/phase-2-seams.md` ③.1) are deferred to the Phase 2/3 boundary (the first
*parallel* node is Phase 3's Generators), and ADR 0063 (loop-budget threading) is sequenced
after Phase 2 unless the architect pulls it in.

**Foundations already in code (Phase 1 forward-investment).** Phase 2 agents and validators
*inherit* these — do not rebuild them: `ToolUsingAgent` (ADR 0072), `Finding[CodeT]`/
`FindingResult[F]` (ADR 0073), verify-only tool sets (ADR 0078), targeted-patch refinement
(ADR 0054), global iteration cap + recursion backstop (ADR 0056), the orchestrator-owned
mechanical-validator stack (ADR 0051), and the shared run-persistence service (ADR 0068).

**Execution log.** Every task ends by appending an entry to `dev/phase-2-execution-log.md`
(template at the bottom). The log is how Phase 2's lessons feed Phase 3's brief, and where
doc-vs-code drift gets surfaced for the next brief-writer. Keep entries terse.

**Corrected citations.** Several architecture-doc lines pre-date Phase-1 ADRs and are stale.
This brief uses the *corrected* targets inline, flagged **[corrected]** with the owning ADR.
Do not "fix" the brief back to the stale doc value — the ADR is authoritative until the
architect updates the doc (which is Task 0's job). The live drift this phase cares about:
- `agents.md §5.5` ("Extractor-Jury Tools. Same as Extractor") and `§5.8` ("Planner-Jury
  Tools. Same as Planner") — **superseded by ADR 0078**: juries get a **verify-only** tool set
  (read/verify via `external_lookup`; no `propose_*`).
- `agents.md §5.7` / `implementation-plan.md §5.2` ("Tools: same as Extractor" for the Planner)
  — read as the **post-0078 producer** contract (read tools + scoped `propose_facet`), not
  "Extractor-minus-propose."

---

## Task 0 (prep): Architect doc reconciliation

**Goal:** Close the doc-vs-code drift Phase 2's reading paths depend on, before the tasks that
read those docs run. This is an **architect/maintainer task**, not implementation — it edits
`docs/`, which implementation tasks may not touch.

**Required reading:** `dev/phase-2-seams.md` §2–§3 (the tracked deferrals and doc mirrors);
ADR 0078 (verify-only tool set); ADR 0069 (spec-version / `SpecEnvelope` deferral).

**Work (architect):**

1. **`plan` verb.** The CLI surface (and any doc enumerating the four verbs) does not list
   `plan`; `implementation-plan.md §2` does. Decision locked: `plan` is a **new top-level verb**
   (`extract → plan → generate`). Reconcile the verb-set listing in the docs accordingly.
2. **Tool-inventory drift → ADR 0078.** Update `agents.md §5.5` (Extractor-Jury) and `§5.8`
   (Planner-Jury) "same tool inventory" lines to the verify-only contract; clarify `§5.7`'s
   Planner tools as the producer contract. These are the lines Tasks 3/4 read.
3. **Exit-criterion blog count.** `§5.5` reads "valid LabManifest for ≥4 of 5 Phase-1 blogs,"
   but the Phase-1 curated set (`eval/blog-sets/manifest.yaml`) has **3** blogs, one synthetic
   (`long-multi-stage-cloud-campaign`, TBD URL). Reconcile the count (and whether the synthetic
   fixture counts) against reality, so Task 10's exit gate is satisfiable.
4. **Tracked doc mirrors** (`dev/phase-2-seams.md` §3): the `material_discrepancies` block mirror
   in `schema-details.md §4`, and the `BaseModel`→`ArtifactModel` doc sweep (code already uses
   `ArtifactModel` per ADR 0004 — docs-only drift).

**Exit criteria:** The doc edits are committed; `just verify` stays green; Tasks 3/4 read
correct (post-0078) tool-inventory text.

**Output notes:** Append a Task 0 entry to `dev/phase-2-execution-log.md`.

---

# Wave 1 — thin vertical slice (→ runnable `plan` on one blog, non-proposing)

## Task 1: Full LabManifest schema + `SpecEnvelope` base

**Goal:** The complete `LabManifest` Pydantic models — the **single source of truth every
Phase 3+ agent reads.** Extract the deferred `SpecEnvelope` base here (LabManifest is its real
second use) so the no-migration load gate dispatches on `spec_kind`.

**Required reading:**

- *Primary:* `schema-details.md §5` (the LabManifest Pydantic shape — CoreBlock, PhaseBlock,
  StepBlock, PrereqBlock, InputBlock, LabResourceBlock, OutputBlock, ProducesWorldState),
  `§5.1` (CoreBlock + ReproducibilityBlock), `§5.5` (PhaseBlock fields), `§5.6` (StepBlock),
  `§2.2` (closed enums: `StepComposition`, `IdentifierKind`, `OnDependencyFailure`, `LabRole`,
  `ReproducibilityTier`, `ProvisioningMechanism`, `DetectionFormat`).
- *Architectural:* `schema.md §4.4` (LabManifest envelope), `§4.5` (PhaseBlock semantics:
  `on_dependency_failure` default `warn`, `identifier_kind` static/runtime_generated discriminator),
  `§4.8` (lab-level reproducibility is *derived*, not authored).
- *Convention:* `dev/decisions/0004-base-class-discipline.md` (`ArtifactModel`); `ADR 0069`
  (spec_version / spec_kind / load gate); `dev/phase-2-seams.md §2` (the `SpecEnvelope` deferral
  — "extract a thin `SpecEnvelope` base at Phase-2's second use").

**Inputs:** Phase 1 complete (AttackSpec, `ArtifactModel`, `Provenance[T]`, registries, the
spec-version machinery from ADR 0069 all shipped and green). Note: `StepBlock` is **manifest-only
and new in Phase 2** (`schema-details.md §5.6`) — Phase 1 built `ChainStep` (the AttackSpec's
narrative step), a different model; do not expect to reuse it.

**Work:**

1. Build every LabManifest block per `schema-details.md §5`. `LabResourceBlock` carries the
   `lab_role: list[LabRole]` list and optional `role_notes`. `PhaseBlock` carries
   `step_composition`, `execution_context`, `on_dependency_failure` (default `WARN`),
   `bind_inputs`, `produces_world_state`, `provisioning_mechanism`, `steps`.
2. `ProducesWorldState` enforces the `identifier_kind` **XOR** in a validator: `STATIC` requires
   `identifier`; `RUNTIME_GENERATED` requires `identifier_source` (a path into the phase's
   declared outputs). Prevents cleanup orphaning downstream.
3. Extract `SpecEnvelope` (`spec_version`, `spec_kind`, `source` + version machinery) as a base
   shared by `AttackSpec` and `LabManifest`; the load gate dispatches on `spec_kind`. Refactor
   `AttackSpec` onto the base **without changing its on-disk shape** (no migration — `§0.6`).
4. Content fields carry `Provenance[T]`; structural fields (ids, paths, type refs, function
   names) do **not** (per `schema.md §4.9`).
5. Tests: every block round-trips Python → YAML → Python; `extra="forbid"` rejects unknown
   fields; the `identifier_kind` XOR rejects both the missing-`identifier` and the
   missing-`identifier_source` cases; a representative **multi-phase** manifest (≥2 phases, a
   multi-role `lab_resources` entry, both `identifier_kind` values) builds and round-trips; the
   load gate routes `AttackSpec` and `LabManifest` by `spec_kind`.

**Exit criteria:** All LabManifest schema tests pass; pyright strict clean for
`cyberlab_gen/schemas/`; a representative manifest round-trips to an equal instance; `AttackSpec`
load/round-trip unchanged after the `SpecEnvelope` refactor.

**Decision discretion:** Internal module layout under `schemas/`; helper constructors.

**No discretion on:** Field names, types, validator logic, `Provenance[T]` wrapping, the enum
sets — all from `schema-details.md §5` / `§2.2`. The `identifier_kind` XOR. No migration of
existing AttackSpec on-disk shape.

**The manifest lock (read this).** Once Task 1 lands, the LabManifest shape is **locked**. The
shape is specified by the docs, not designed here. Discipline for anything that "feels awkward"
later: route it to `dev/manifest-friction.md` and revisit at the Phase-4 review — **do not change
the schema.** The *one* legitimate exception is genuine **first-consumer structural infeasibility**
— Task 3's Planner (the first real consumer) demonstrably cannot emit a valid manifest for a
construct the AttackSpec established. That is a **docs gap**, not an implementer's call: stop,
record an ADR with the specific infeasibility, and escalate to the architect. Never edit the
schema unilaterally to make the Planner "work better."

**Output notes:** Append a Task 1 entry to `dev/phase-2-execution-log.md`.

---

## Task 2: Lab-level reproducibility derivation

**Goal:** Small deterministic framework code that derives `core.reproducibility` from the
Planner's per-step values via the any-heterogeneity-mixed rule. Framework, never the Planner
(`architecture.md §1.5`: LLMs don't compute their own behavior).

**Required reading:** `schema.md §4.8` (the derivation rule: all-same-tier → that tier; steps
spanning tiers → `mixed`, regardless of proportions); `agents.md §5.7` ("lab-level
`reproducibility` derived" — the Planner emits per-step, the framework derives lab-level).

**Inputs:** Task 1 (the `ReproducibilityBlock` + per-step `ReproducibilityTier` fields exist).

**Work:**

1. A pure function: list of per-step `ReproducibilityTier` → lab-level classification
   (`full` / `partial_simulation` / `demonstration_only` / `mixed`, plus the `n/a` handling).
2. Wire it as a post-Planner framework step (runs after the Planner emits, before the manifest is
   finalized). No agent involvement.
3. Tests cover every rule branch: all-`full`; all-`demonstration_only`; a heterogeneous mix →
   `mixed`; single-step labs; the all-`n/a` edge.

**Exit criteria:** The any-heterogeneity rule is correct on **every** test case (a hard `§5.5`
exit criterion); pure-function, no I/O; pyright strict clean.

**No discretion on:** The derivation rule (`schema.md §4.8`). Derivation is framework code, not
Planner output.

**Output notes:** Append a Task 2 entry to `dev/phase-2-execution-log.md`.

---

## Task 3: Planner agent (non-proposing for the slice)

**Goal:** The Planner: enriched AttackSpec + user config → draft LabManifest skeleton. For this
slice it is **constrained non-proposing** — `propose_facet` is deferred to Task 7 (which clears
the hardcoded-extractor proposal blocker). This dodges the construction-time rejection of the
Planner's runtime facets while the spine is proven.

**Required reading:**

- *Primary:* `agents.md §5.7` (Planner job, inputs, outputs, provenance discipline, quality bar,
  failure modes; the emergent-lab-class principle); `pipeline.md §3.2.6` (Planner stage).
- *Tools* **[corrected, ADR 0078]:** the Planner is a **producer**: read tools (`external_data_sources`
  lookups, `query_value_types_registry`) for THIS slice. `propose_facet` (scoped to `runtime:*` and
  lab-derived `lab_class_signal:*`) is **deferred to Task 7** — do not wire it here. Read `§5.7`'s
  tool list as the producer contract, not "same as Extractor."
- *Contract:* `dev/decisions/0072-tool-using-agent-contract.md` (the Planner **subclasses**
  `ToolUsingAgent` — registries guard, executor, run-with-tools all inherited);
  `architecture.md §1.5`, `§0.7` (emergent lab class — per-step ladder, no master class).
- *Provider:* `pipeline.md §3.5` (capability hint not model name; base-prompt-plus-overlay).

**Inputs:** Tasks 1, 2; `ToolUsingAgent` (in code). A real enriched AttackSpec from Phase 1's
`extract` (stub enrichment is fine — the manifest *shape* does not depend on enrichment richness).

**Work:**

1. Subclass `ToolUsingAgent`; capability-hint dispatch; prompt as base + overlay files.
2. Emit the draft LabManifest skeleton: phases (steps, no `implementation.path`), `lab_resources`
   with `lab_role`, prereqs (pre_lab/mid_lab), inputs, outputs, `produces_world_state`, declared
   facets (existing ones only this slice), re-keyed per-phase excerpt bundles, **per-step**
   reproducibility carried forward unchanged.
3. Boundary enforcement (hard): the Planner does **not** propose value types (Extractor authority);
   does **not** repair AttackSpec content; does **not** re-evaluate per-step reproducibility.
4. Planner inferences (phase grouping, `step_composition`, `execution_context`,
   `provisioning_mechanism`, `on_dependency_failure`) carry `source: llm_inference` with AttackSpec
   citations.
5. Tests: against a mock provider (reuse the Phase-1 pattern), the Planner produces a
   schema-valid manifest from a fixture AttackSpec; the per-step reproducibility pass-through is
   asserted (input tiers == manifest tiers); an attempt to emit an untyped input fails the quality
   bar.

**Exit criteria:** The Planner returns a Layer-1-valid `LabManifest` for the fixture AttackSpec;
no concrete model name in agent code; pyright strict clean.

**Decision discretion:** Prompt wording/structure; how excerpt bundles are re-keyed internally.

**No discretion on:** No value-type proposals, no AttackSpec repair, no reproducibility
re-evaluation (`agents.md §5.7`). Producer-not-jury tool set. Emergent lab class — no master
class field.

**Output notes:** Append a Task 3 entry to `dev/phase-2-execution-log.md`.

---

## Task 4: Planner-Jury (verify-only) + refinement extension + route-back

**Goal:** The second jury (verify-only per ADR 0078) and the refinement-coordinator extension:
Planner↔Jury (reusing ADR 0054 targeted-patch) **and the Planner-failure → Extractor route-back.**
Route-back is the #1 named risk (`§5.6`) and a hard exit criterion — proven here, on the slice,
not last.

**Required reading:**

- *Primary:* `agents.md §5.8` (Planner-Jury: reviews fidelity, phase decomposition, facet
  correctness; asymmetric calibration); `pipeline.md §3.2.7` (Planner-Jury stage).
- *Tools* **[corrected, ADR 0078]:** **verify-only** (`external_lookup` to verify external_api
  responses; no `propose_*`). Inherited via the `ToolUsingAgent` verify-only hook — **not** "same
  as Planner" (`§5.8` is stale).
- *Refinement:* `dev/decisions/0054-targeted-patch-refinement-mechanism.md` (jury-revise →
  prior spec + field feedback → patch of flagged paths; deep-set + whole-spec re-validate);
  `dev/decisions/0056-global-iteration-cap-and-recursion-limit.md` (caps); `dev/decisions/
  0051-one-orchestrator-owned-mechanical-validator-stack.md` (the orchestrator routes findings).
- *Boundary:* `agents.md §5.7` failure modes ("the Planner does not repair AttackSpec content";
  AttackSpec incoherence routes back to the Extractor).

**Inputs:** Task 3 (Planner). The refinement coordinator, caps, and targeted-patch mechanism are
in code from Phase 1.

**Work:**

1. Planner-Jury subclassing `ToolUsingAgent` with the verify-only tool set; verdict shape mirrors
   Extractor-Jury (`approve` / `revise` with field feedback / `reject`); asymmetric calibration
   (tune **up** on false-approval, never down on false-rejection). The threshold *number* stays a
   **placeholder** (mirror the Extractor-Jury 0.7 default); it is locked by the architect's eval
   run (`§5.4`, recorded in `CALIBRATION.md` — see Task 10), **not** by Task 4. Task 4 builds the
   asymmetric *discipline*, not the value.
2. Extend the refinement coordinator: Planner↔Planner-Jury revise loop reuses the ADR-0054 patch
   path (Planner force-emits a patch for flagged manifest fields only). Same
   disagreement-without-progress handling as the Extractor-Jury (`§5.8`): exhausted `revise` →
   proceed with `low_jury_confidence`; `reject` → halt.
3. The **route-back**: when the Planner detects AttackSpec incoherence (mismatched
   precondition/postcondition; a missing value type), it flags with structured detail and the
   coordinator routes **back to the Extractor** — the Planner never repairs the AttackSpec. Per-agent
   + total caps stay at the Phase-1 placeholders; full cycle/cascade detection is **Phase-4 deferred**.
4. Tests: a fixture with a deliberately **incoherent** AttackSpec drives a route-back to the
   Extractor (asserted — the Planner does not "fix" it); a jury `revise` on a manifest field drives
   a targeted patch that leaves unflagged fields byte-identical; exhausted-revise proceeds with the
   low-confidence flag.

**Exit criteria:** Route-back to the Extractor is asserted on an incoherent-AttackSpec fixture
(`§5.5` exit criterion); the targeted-patch loop converges on a jury-revise fixture; pyright strict
clean.

**No discretion on:** Verify-only jury tools (ADR 0078). Planner never repairs the AttackSpec
(`§5.6` risk). Retry-vs-refinement split — route-back/revise is refinement, structural malformation
is retry.

**Output notes:** Append a Task 4 entry to `dev/phase-2-execution-log.md`.

---

## Task 5: Validator Layer 2 (cross-block-within-manifest)

**Goal:** Layer 2 over the LabManifest, subclassing the ADR-0073 `Finding`/`FindingResult` base.
In Phase 2 the **cross-block-within-manifest** checks are live; the **code-vs-manifest** checks are
built but inert (no code until Phase 3). Layer 2 **flags findings; it never mutates the manifest.**

**Required reading:**

- *Primary:* `validation.md §6.5` (Layer 2 — cross-checks, facet `implies`/`incompatible_with`
  enforcement as findings not mutation, `references_lab_outputs` bidirectional,
  `produces_world_state.identifier_source` resolution, `affected_platforms` consistency); `§6.10`
  (refinement-loop integration — Layer 2 findings route to the responsible agent).
- *Contract:* `dev/decisions/0073-validator-finding-result-contract.md` (subclass
  `Finding[L2Code: StrEnum]` / `FindingResult` — define the L2 code vocabulary; `render()` and
  locators inherited); `dev/decisions/0074-finding-locators-integer-indices.md` (locator
  convention); `dev/decisions/0051-...md` (the orchestrator owns and routes the stack).
- *Schema cross-refs:* `schema.md §4.13` (facet `implies`/`incompatible_with`), `§4.5`
  (`identifier_source` resolution, `references_lab_outputs`).

**Inputs:** Task 1 (manifest), Task 3 (Planner output to validate), ADR 0073 base (in code).

**Work:**

1. Define `L2Code` (the closed finding-code vocabulary for Layer 2) and subclass the base.
2. Live cross-block checks: facet `implies` (missing implied facet → **finding**, routed to the
   Planner; never auto-added), `incompatible_with` (hard finding); `produces_world_state` with
   `identifier_kind: runtime_generated` has an `identifier_source` resolving to a declared phase
   output; `affected_platforms` (if present) consistent with `target:*` facets.
3. Build `references_lab_outputs` bidirectional cross-check as framework code but **inert** (no
   per-phase / lab-level code exists in Phase 2 — it lights up in Phase 3). Mark it clearly so
   Phase 3 wires it without re-deriving.
4. Wire Layer 2 into the orchestrator-owned mechanical stack (ADR 0051); findings route via the
   coordinator to the Planner for a re-run.
5. Tests: a manifest missing an implied facet yields the finding and routes to the Planner (no
   mutation); an `incompatible_with` pair yields a hard finding; a dangling `identifier_source`
   yields a finding; the manifest is **unchanged** after Layer 2 runs (read-only assertion).

**Exit criteria:** The live cross-block checks fire correctly on crafted manifests; Layer 2 never
mutates the manifest; the inert code-vs-manifest checks exist and are marked Phase-3; pyright strict
clean.

**No discretion on:** Read-only validator (findings, never mutation — `§6.5`, `§1.6`). Subclass the
ADR-0073 base; do not invent a bespoke finding pair. Mechanical, never LLM (`§1.6`).

**Output notes:** Append a Task 5 entry to `dev/phase-2-execution-log.md`.

---

## Task 6: `plan` verb + orchestrator wiring + persistence (slice end-to-end)

**Goal:** `cyberlab-gen plan <attack-spec.yaml>` runs the linear graph Planner → Planner-Jury →
Layer 2 and persists the run, producing a valid `lab.yaml` for **one representative blog**. This is
where the slice becomes runnable.

**Required reading:**

- *Primary:* `pipeline.md §3.2.6`–`§3.2.7` (the Planner stages), `§3.1` (deterministic state
  machine, typed cross-stage boundaries); `cli/extract.py` (the existing verb pattern to mirror).
- *Persistence:* `dev/decisions/0068-shared-run-persistence-service.md` (reuse
  `persist_pipeline_artifacts` — the billed-model invariant must not be copied a third time);
  `dev/decisions/0039-artifact-persistence-run-store.md`.
- *Caps/graph:* `dev/decisions/0056-...md` (the iteration cap binds the Planner loop too).

**Inputs:** Tasks 1–5. Shared persistence (ADR 0068), the orchestrator graph (in code). **The
slice fixture:** a real, jury-approved codebuild AttackSpec already exists from a Phase-1 run
(`~/.cyberlab-gen/runs/20260610T040636Z-www-wiz-io-wiz-research-codebreach-vulnerability/spec.yaml`
— `approve`, all dimensions clear the 0.7 floor, in-scope, 5 chain steps, **CVE-less** so its value
survives stub enrichment). Promote that `spec.yaml` into the repo as a committed test fixture; **no
new paid `extract` run is needed.**

**Work:**

1. Add `plan` as a **new top-level verb** consuming a validated `attack-spec.yaml` (the Phase-1
   `extract` output) and writing `lab.yaml`. Mirror `extract`'s structure; do **not** reach into
   orchestrator privates (the seams file flags that CLI→orchestrator-private debt — keep the new verb
   clean by carrying what it needs through the typed outcome).
2. Wire the linear graph: Planner node → Planner-Jury node → Layer 2, with the
   refinement/route-back edges from Task 4. **No `Stage`/`Node` abstraction, no reducer channels** —
   the graph is linear; that abstraction is the Phase 2/3 boundary.
3. Reuse `persist_pipeline_artifacts` for the `plan` run (spec, jury verdict, cost, run.json with
   the ledger-stamped billed model). Save on every exit path (ADR 0039/0053).
4. Slice blog: **`aws-codebuild-actor-id-regex-bypass`** (medium complexity, 5 steps,
   enrichment-independent narrative — its value survives stub enrichment, so a thin Planner output
   isn't mistaken for a broken Planner). Use the promoted codebuild `spec.yaml` fixture (see Inputs)
   and drive it end-to-end through `plan`.
5. Tests: an integration test runs `plan` on the slice blog's AttackSpec fixture and asserts a
   schema-valid `lab.yaml` plus a persisted run; cost is recorded through the ledger.

**Exit criteria:** `cyberlab-gen plan <codebuild-attack-spec>` writes a Layer-1+Layer-2-valid
`lab.yaml` and a persisted run; `just verify` green; the manifest shape is now validated against a
real Planner output (Wave 1's purpose met).

**Decision discretion:** Output path conventions for `lab.yaml`; the integration-test fixture shape.

**No discretion on:** Linear graph (no Stage/Node this phase). Reuse shared persistence (no third
billed-model copy). New top-level `plan` verb.

**Output notes:** Append a Task 6 entry to `dev/phase-2-execution-log.md`. Record any manifest
friction in `dev/manifest-friction.md` and any `identifier_kind` edge cases in
`dev/identifier-kind-edge-cases.md` (do **not** change the schema — see the Task 1 lock).

---

# Wave 2 — broadening (→ exit criteria)

## Task 7: Generalize the proposal path + clear the two hard blockers

**Goal:** Make proposals agent-agnostic so the Planner can propose, then turn on the Planner's
scoped `propose_facet`. Two structural blockers must fall first: proposal machinery hardcoded to
`"extractor"`, and the stale `MergedRegistries` snapshot.

**Required reading:** `dev/phase-2-seams.md §2` (the proposal-authority, generic-accept,
stale-snapshot, and dedup items); `schema.md §4.16` (proposal lifecycle, promotion gated on
shipping), `§4.13` (facet authorship split — `runtime:*` and lab-derived `lab_class_signal:*` are
the Planner's); `dev/decisions/0044-...md` / `0045-...md` (the propose→approve→overlay→validate
loop and runtime-proposable types); `dev/decisions/0062-...md` (promotion on ship + registry digest).

**Inputs:** Wave 1 complete.

**Work:**

1. A `Proposal` protocol → one **generic** accept path (replaces the parallel-by-hand `accept_*`).
2. Stamp `proposed_by` at the **framework accept boundary**; make the `EXTRACTOR_FACET_CATEGORIES`
   gate a **per-agent authority input** (so the Planner's `runtime:*` facets aren't rejected at
   construction).
3. **Stale-snapshot invalidation:** re-read / invalidate the `MergedRegistries` snapshot after an
   overlay write, so the Planner (second proposer) sees just-accepted entries.
4. Mechanical dedup: an accept-time "already registered?" check against the merged registry (the
   Planner is a second proposer; collisions can't silently shadow bundled entries).
5. Enable the Planner's `propose_facet` (Task 3 deferral) for `runtime:*` and lab-derived
   `lab_class_signal:*`, plus execution_context proposals per `agents.md §5.7`.
6. Tests: the Planner proposes a `runtime:*` facet that is accepted, stamped `proposed_by=planner`,
   visible in the post-write snapshot; a colliding proposal is mechanically rejected; the Extractor's
   existing proposal path is unchanged (regression).

**Exit criteria:** The Planner proposes and the framework accepts/stamps/dedups generically; the
Extractor path regresses cleanly; pyright strict clean.

**No discretion on:** Proposal never mutates shared state until the spec ships (`§4.16`). Authority
is a per-agent input, not a hardcoded literal.

**Output notes:** Append a Task 7 entry to `dev/phase-2-execution-log.md`.

---

## Task 8: Post-Planner interactive interrupt

**Goal:** The post-Planner interrupt — the four-option menu plus per-proposal Accept/Edit — mirroring
the Phase-1 post-Extractor interrupt. Interactive mode only.

**Required reading:** `pipeline.md §3.2.8` (post-Planner interrupt; the `references_lab_outputs`
surface is inert in Phase 2); `dev/decisions/0024-...md` (the Phase-1 interrupt seam to mirror);
`dev/decisions/0044-...md` (the per-proposal Accept/Edit machinery).

**Inputs:** Tasks 6, 7.

**Work:**

1. Four-option menu (approve / natural-language feedback / `$EDITOR` / abort), reusing the
   post-Extractor interrupt machinery.
2. Per-proposal Accept/Edit for Planner facet proposals (edits revalidated through Layer 1/2).
3. Keep the `references_lab_outputs` surface inert (no code in Phase 2).
4. Tests: each menu path; an edited proposal is revalidated; `--auto` skips the interrupt.

**Exit criteria:** The interrupt drives all four paths and per-proposal Accept/Edit; `--auto`
bypasses it; `just verify` green.

**Output notes:** Append a Task 8 entry to `dev/phase-2-execution-log.md`.

---

## Task 9: Full pre-Planner enrichment (data-driven)

**Goal:** Replace the Phase-1 single-source NVD stub with **data-driven** enrichment: dispatch from
registry `enrichment_triggers` to per-source adapters (NVD the first adapter behind the seam), wire
MSRC/OSV.dev/KEV/EPSS/security-bulletins, and implement materiality classification. This is the
landing point for ADR 0077 (`dev/phase-2-seams.md` ③.2).

**Required reading:** `dev/phase-2-seams.md` ③.2 + ADR 0077 (the external-source work-stream — inert
CVE ship-gate, `source_of_record`, `advisory.source` retype, the `NvdClient`→neutral-ports-module
move); `pipeline.md §3.2.4` (enrichment — framework-only authorship, materiality-scaled surfacing,
budget, rate-limiting); `schema.md §4.9` (framework sets `source: external_api`), `§4.14`
(`external_data_sources` vs `static_catalogs`, the `discrepancy_materiality_rules` field).

**Inputs:** Wave 1; the `external_data_sources` registry (exists).

**Work:**

1. Drive enrichment from declared `enrichment_triggers`; resolve a per-source adapter per source.
   NVD is the first concrete adapter behind the seam (no more hardcoded `_NVD_SOURCE_ID` dispatch).
2. Move the `NvdClient` Protocol out of `framework.enrichment` to a neutral ports module (agents +
   validators import it).
3. Wire MSRC, OSV.dev, KEV, EPSS, security bulletins as real triggered lookups.
4. Materiality classification per each source's `discrepancy_materiality_rules`. The framework (never
   an agent) sets `source: external_api` and records discrepancies. Material-discrepancy *surfacing at
   the interrupt* is **Phase-4 deferred** — implement the classification only.
5. Land the ADR-0077 items: the inert CVE ship-gate, `source_of_record` check, `advisory.source`
   retype.
6. Tests: a triggered source resolves to its adapter and enriches; a recorded API/blog disagreement
   classifies material vs non-material per the rules; an unavailable source is non-fatal
   (ADR 0042); the Extractor's existing enrichment path regresses cleanly.

**Exit criteria:** Enrichment is data-driven (no hardcoded source dispatch); the five new sources
enrich on recorded fixtures; materiality classification works on a real disagreement (`§5.5`);
unavailable sources never fatal.

**No discretion on:** Framework-only authorship of `external_api` (`§4.9`). External-source
unavailability is never fatal (ADR 0042).

**Output notes:** Append a Task 9 entry to `dev/phase-2-execution-log.md`.

---

## Task 10: Eval harness additions + curated-set growth

**Goal:** The Phase-2 eval metrics and the grown curated set. **The harness and set are built here;
the architect runs the paid calibration passes** (eval is user-run, real money) and records the
locked values in `CALIBRATION.md`.

**Required reading:** `implementation-plan.md §5.3` (curated-set growth), `§5.4` (calibration items),
`eval.md §7.4` (mechanical metrics); `dev/decisions/0025-...md` / `0028-...md` / `0030-...md`
(the Phase-1 eval harness shape, resilience, spend guards to extend).

**Inputs:** All prior tasks (the things being measured); the Phase-1 harness + `CALIBRATION.md`.

**Work:**

1. Metrics: manifest field-coverage; per-step reproducibility distribution; lab-level
   reproducibility classification (using Task 2's rule); Planner-Jury false-approval/false-rejection
   review tooling.
2. Grow the curated set to 8–10 (reconciled against Task 0's blog-count fix): at least one
   multi-cloud, a vulnerability-disclosure with a substantive `vulnerability_story`, a `mixed`
   reproducibility example, and a blog that triggers a Planner `runtime:*` facet proposal. Each new
   blog gets a manual walk under `dev/curated-blog-walks/` (enforced by `tests/eval/test_manifest.py`).
3. Leave calibration **placeholders** live (Planner token budget, per-stage retry, Planner-Jury
   asymmetric threshold, `on_dependency_failure` default `warn`, pre-Planner API budget default 100);
   the architect's eval run locks them.
4. Tests: the new metrics compute on a fixture run; every `walk:` path resolves.

**Exit criteria:** The metrics compute; the curated set is grown with resolving walks; the harness is
ready for the architect's calibration run. (The paid run + `CALIBRATION.md` lock is the architect's
step, not this task's.)

**No discretion on:** Do **not** run the provider-backed eval — that is the architect's, with real
money. Build the harness + set only.

**Output notes:** Append a Task 10 entry to `dev/phase-2-execution-log.md`.

---

## Sequencing summary

```
Task 0 (architect doc reconciliation) ──┐ (do before the tasks that read those docs)
                                         │
Wave 1 (thin slice, non-proposing):      ▼
  Task 1 (LabManifest + SpecEnvelope) ──► Task 2 (reproducibility derivation)
        │                                      │
        └──────────────► Task 3 (Planner) ◄────┘
                              │
                              ├──► Task 4 (Planner-Jury + route-back)
                              └──► Task 5 (Layer 2)
                                        │
                              Task 6 (plan verb + wiring + persistence) ──► runnable `plan`

Wave 2 (broadening):
  Task 7 (generic proposals) ──► Task 8 (post-Planner interrupt)
  Task 9 (full enrichment, data-driven)        [orthogonal to the spine; after Wave 1]
  Task 10 (eval + curated set)                 [last; measures everything]
```

Wave-1 tasks 1–2 can parallelize; 4 and 5 can parallelize after 3. Wave 2 tasks are
largely independent; 10 is last.

## Calibration items locked in Phase 2 (architect's eval run; `§5.4`)

Planner token budget; Planner per-stage retry count; Planner-Jury threshold (asymmetric);
`on_dependency_failure` default confirmed `warn`; per-run auto-accepted-proposal cap (now the
Planner contributes too); pre-Planner external API budget (default 100). Recorded with evidence in
`CALIBRATION.md`. Tag **`v0.3`** at phase exit.

## Exit criteria (`§5.5`, reconciled in Task 0)

- `cyberlab-gen plan` produces a valid LabManifest for the reconciled count of Phase-1 blogs + ≥2
  Phase-2 additions.
- Layer 1 + Layer 2 cross-block checks pass on ≥90% of curated runs.
- Lab-level reproducibility classification correct per the any-heterogeneity rule on every test case.
- `lab_role` lists populate sensibly for at least one multi-role example.
- The Planner routes back to the Extractor on AttackSpec incoherence (does **not** repair).
- Materiality-based discrepancy classification works on real blog/API disagreement.
- `CALIBRATION.md` records the Planner-Jury threshold with evidence.

## Risks (`§5.6` + seams)

- **Manifest-shape instability** — the dominant risk. Lock at Task 1; route friction to
  `dev/manifest-friction.md`; the only reopening is architect-escalated first-consumer infeasibility.
- **Planner doing too much** — must route AttackSpec incoherence back, never repair. Tested on an
  incoherent fixture in Task 4, not assumed.
- **Hardcoded-extractor proposal machinery** — a hard blocker the Planner can't propose around;
  cleared in Task 7 before proposing is enabled.
- **Stale `MergedRegistries` snapshot** — silently wrong for the second proposer; invalidated in
  Task 7.
- **`identifier_kind` edge cases** ("static-with-environment-substitution") — log to
  `dev/identifier-kind-edge-cases.md`; the schema enforces XOR and Layer 2 depends on it.

---

## Execution-log entry template

Append one block per task to `dev/phase-2-execution-log.md`:

```
## Task N: <title>  (<date>)

**Built:** <what shipped — files, models, tests>
**Decisions:** <ADRs opened, with numbers>
**Surprises / drift:** <doc-vs-code drift, friction logged, anything the next task should know>
**Deferred:** <anything intentionally not done, with the owning task/phase>
**Verify:** <just verify result>
```
