# 0099 — Generic proposal accept-path + the proposing Planner (Task 7)

Status: accepted
Date: 2026-06-18
Supersedes/relates: ADR 0021 (proposals), 0044/0045 (propose→accept→overlay loop), 0050/0062
(promotion on ship), 0078 (verify-only jury), 0089 (the `ToolUsingAgent` tool-provider hook),
0097 (the plan-verb cross-check ship gate).

## Context

Phase-2 Task 7 makes proposals **agent-agnostic** so the Planner can propose, then turns on the
Planner's scoped `propose_facet`. `dev/phase-2-seams.md §2` names the structural blockers:

1. Proposal machinery hardcoded to `"extractor"` (`proposed_by='extractor'` literals; the
   `EXTRACTOR_FACET_CATEGORIES` gate baked into the executor; `ProposedFacet.category` literal).
2. Acceptance is parallel-by-hand per type (three near-identical `accept_*` + copy-pasted CLI loops).
3. No mechanical dedup of a proposal against the merged registry before acceptance — a proposal
   colliding with a **bundled** entry silently shadows it (overlay-wins).
4. The in-process `MergedRegistries` snapshot goes stale after an overlay write — benign today (only
   the Extractor proposes, post-ship), but the Planner is a *second* proposer in `generate`.

Unit 1 (already landed) widened `ProposedFacet.category` to admit `runtime`, added the per-agent
authority sets (`EXTRACTOR_FACET_CATEGORIES` / `PLANNER_FACET_CATEGORIES`) and the `ProposerAgent`
alias, and parameterized every `to_entry` with a framework-supplied `proposed_by`.

The user's rulings scoping this task: **facet-only** (no execution-context proposals this task);
proposals are **captured/persisted but NOT promoted** — promotion stays Task 8; **build the
accept-path generalization now** (the brief's literal Task-7 scope); the route-back auto-loop stays
deferred (its only sound home is `generate`, a Phase-3 stub).

## Decision

### 1. A minimal `Proposal` protocol; the registry-shape table lives in the framework

`Proposal` (in `agents/proposals.py`) is the **behavioral** surface the framework needs: `reasoning`
plus `to_entry(*, proposed_by, proposed_in_run=None) -> ArtifactModel`. It carries **no**
overlay/registry-internal knowledge — the agent-facing model stays clean. The per-type metadata
(overlay filename, a human label, and the dedup accessor) lives in a single `entry-type → (...)`
table in `framework/proposal_acceptance.py`, keyed by the concrete entry type `to_entry` returns. The
registry key is read off the entry's `ENTRY_KEY_FIELD` ClassVar (the one home, matching the overlay
writer / merge). `ProposedFacet.to_entry` gains an ignored `proposed_in_run` so the three proposals
share one signature (`FacetEntry` has no run field).

### 2. One generic accept path; `proposed_by`/origin stamped at the framework boundary

`accept_proposal(proposal, ctx, *, approval)` is the single pure write (replaces `accept_value_type`
/ `accept_facet` / `accept_thesis_type`). `accept_proposals(proposals, ctx, *, approval, registries,
cap)` is the batch with dedup + cap; `auto_accept_to_overlay` is kept as a thin order-preserving
wrapper (value-types → facets → thesis-types) so the CLI call site stays byte-stable.
`AcceptanceContext` now carries `proposed_by: ProposerAgent`, `proposal_origin`, and `source_lab`
(was hardcoded in `_audit`) — the framework stamps them, never the agent (`schema.md §4.16`,
`architecture.md §1.5`). The Extractor builds the context with `proposed_by="extractor"`,
`proposal_origin="llm_during_extraction"`, `source_lab=None` → its overlay output is byte-identical.

### 3. Mechanical dedup = merged-registry + intra-batch

Accept-time dedup rejects (does **not** write) a proposal whose key already resolves in the merged
registry (the shadowing guard the seam asks for) **or** was already accepted earlier in the same
batch (so two same-named proposals in one run don't both write — the intra-batch stale-snapshot case,
handled with a running key-set rather than an expensive mid-batch reload). Skipped proposals are
reported in `AcceptanceResult.skipped`, distinct from over-cap `deferred` (ADR 0050/0062: neither is
a hard halt). Dedup is a no-op when no `registries` snapshot is supplied (the fake-runner test path),
so the Extractor's existing non-colliding runs are unchanged.

Consequence (deliberate, surfaced): "merged" includes the user's *overlay*, so re-proposing a key
that a **prior** run already accepted to the overlay is now a reported skip rather than the
`write_overlay_entry` replace-by-key re-write. The on-disk end-state is identical (the entry is
already there); only the report wording differs. This is correct for the motivating within-run
two-proposer case (the Planner must not duplicate a name the Extractor just accepted, seen via the
reloaded snapshot); correcting an existing overlay entry is a maintainer-edit / replace-by-key
concern, not an agent re-proposal. The replace-by-key path in `write_overlay_entry` is unchanged and
still serves any non-deduped caller.

### 4. Snapshot invalidation = `reload_merged_registries(overlay_dir)`

The cross-process stale-snapshot fix (the Planner seeing the Extractor's just-accepted entries in
`generate`) is `registries/merge.reload_merged_registries(overlay_dir)` — the documented
re-read-after-write seam. It is pinned by a test (accept a facet → reload → visible) but **not** wired
into a live two-proposer flow, because none exists yet: `generate` is a Phase-3 stub and Planner
promotion is Task 8. Wiring a fake consumer now would be unexercised surface.

### 5. The proposing Planner: per-agent authority as executor inputs, not new literals

`ExtractorToolExecutor` gains `facet_categories` (default `EXTRACTOR_FACET_CATEGORIES`),
`facet_authority_hint`, and `refused_propose_tools` (default empty) — the authority is now a
per-agent **input**, not a hardcoded literal. `verify_only` (the Extractor-Jury, ADR 0078) is
unchanged. `PlannerToolExecutor` (still an `ExtractorToolExecutor` subtype, ADR 0089) sets
`facet_categories=PLANNER_FACET_CATEGORIES`, refuses `propose_value_type`/`propose_thesis_type`
(Extractor authority), and overrides `execute` for the new read tool `query_value_types_registry`
(returns `value_types` shapes on demand; read-only, never fatal — mirrors the lookup philosophy).
`planner_tool_definitions` advertises exactly `{external_lookup, propose_facet (runtime:* /
lab-derived lab_class_signal:*), query_value_types_registry}`. The Extractor's advertised four tools
are byte-unchanged.

`PlanResult.facet_proposals` carries the Planner's proposals through state → `PlanPipelineOutcome` →
`PlanRunResult` and is surfaced in the plan report. They are **captured**, never promoted (no overlay
write in the `plan` verb); promotion + the post-Planner interrupt's per-proposal Accept/Edit is Task 8.

### 6. No plan-side provisional resolution is needed this task — because the gate it would feed does not exist (owned deferral)

The original plan reserved a unit for "plan-side provisional resolution so a Planner-proposed
`runtime:*` facet survives the manifest's mechanical validation." Investigation — and then an
exhaustive multi-surface audit (five independent rejection-surface sweeps + an end-to-end trace of a
hallucinated `runtime:typo_xyz`, all reading the code directly, high confidence) — showed **there is
no such gate to survive**:

- The `plan` graph runs **only** `SemanticCrossCheckValidator.validate(manifest)` (`plan_orchestrator.py`
  `cross_check_node`); its two facet checks do `if entry is None: continue` — an unregistered facet is
  **skipped by design** ("a Layer-1 (static-schema) concern, not this layer's", `semantic_cross_check_validator.py:199-201, 231-232`), and `SemanticCrossCheckCode` has no `unknown_facet` member.
- The only registry-membership facet check, `StaticSchemaValidator._check_facets` (`UNKNOWN_FACET`,
  which *does* honour `PendingProposals`), is typed `validate(self, spec: AttackSpec)`, runs only in the
  **extract** orchestrator on the AttackSpec, and is **never constructed by the plan runner**
  (`cli/main.py` wires only the cross-check into `PipelinePlanRunner`). Its docstring anticipated "the
  LabManifest path lands in Phase 2" — never delivered.
- `FacetName` (`primitives.py`) is a pure shape regex (category-prefix + snake_case); it consults no
  registry, so `LabManifest` construction admits `runtime:typo_xyz`. The ship/write path only
  framework-stamps (`model_copy`, no re-validation) and YAML-dumps.

So threading a manifest-side `PendingProposals` now would guard a path nothing rejects — it would be
unexercised machinery. Provisional resolution is therefore correctly *not* built this task.

**This is a recorded, owned deferral, not an emergent gap.**

- **Gap.** No mechanical gate in the `plan` pipeline rejects a manifest facet that resolves in no
  registry (bundled or overlay) between the Planner emitting it and `lab.yaml` being written.
- **Pre-existence.** The gap **pre-existed Task 7** — it is structural (the manifest Layer-1
  membership check was specced at architecture tier, `validation.md §6.4`, but never built; the
  cross-check always skipped unknown facets). Task 7 (the proposing Planner) **widened the
  consequence** — a proposed-but-unpromoted `runtime:*` facet now also ships unresolved — but did not
  create the gap; the Task-7 unit-4 investigation *discovered and recorded* it.
- **Owner.** No Phase-2 task owns it (the phase-2 brief's only validator task is Task 5 = the Layer-2
  cross-check). Its binding home is the **Phase-3 `generate`-pipeline Validator stage** (`pipeline.md
  §3.2.10`), where Layer 1 over the `LabManifest` belongs beside Layers 2/3/5. The concrete deliverable:
  extend `StaticSchemaValidator` to accept a `LabManifest` (or a sibling manifest path) and run a
  Layer-1 registry-membership node **before** the cross-check; and **provisional `PendingProposals`
  resolution for proposed facets must land with it** (so a legitimately-proposed `runtime:*` facet
  provisionally resolves while a typo is rejected). Task 8 (the post-Planner interrupt) is the *wrong*
  owner — it re-validates user edits, it is not the primary producer gate.
- **Interim exposure (Phase 2).** A `plan`-produced `lab.yaml` can declare (1) a hallucinated/typo
  facet (e.g. `target:awss`) or (2) a Planner-proposed-but-unpromoted `runtime:*` facet, either
  resolving in no registry, and still ship. **Blast radius is bounded:** this is vocabulary-resolution
  correctness on a dev/eval command's *skeleton* artifact (ADR 0096) — **not** an `architecture.md
  §1.6` mechanical-safety guard (no credential / Layer-5 bypass). A legitimately-new proposed facet is
  *not* false-positived by any existing check (same `None → continue` skip), so the only issue is the
  absence of the check, not a check firing wrong.
- **Doc reconciliation.** `CLAUDE.md` / ADR 0097's phrasing that the plan slice produces a
  "Layer-1+Layer-2-valid `lab.yaml`" is accurate only in the *weak Pydantic-structural* sense, not the
  `validation.md §6.4` registry-membership sense. `CLAUDE.md` is corrected to "Pydantic-structural +
  Layer-2-(semantic-cross-check)-valid"; ADR 0097 stands as the historical record with this ADR as the
  correction.

## Consequences

- The Extractor proposal path (overlay bytes, audit blocks, advertised tools, rejection messages)
  regresses cleanly — verified by the unchanged extractor-tool / CLI / acceptance assertions.
- The Planner can propose `runtime:*` / lab-derived `lab_class_signal:*` facets; they are captured on
  the outcome, stamped `proposed_by=planner` only if/when a future promotion accepts them.
- Dedup makes a bundled-shadowing proposal a reported skip instead of a silent overlay shadow.
- Deferred: execution-context proposals; Planner promotion + the post-Planner interrupt (Task 8);
  the live two-proposer snapshot reload wiring (`generate`, Phase 3); dropping the `ExtractorTool
  Executor` subclassing once the lookup engine moves to a neutral ports module (Task 9, ADR 0089).
- Deferred (owned, §6 above): **manifest Layer-1 facet-membership validation** — owner Phase-3
  `generate` Validator stage; provisional `PendingProposals` resolution must land with it. Interim:
  an unregistered manifest facet can ship in a `plan`-produced `lab.yaml` (bounded — vocabulary
  correctness on a dev/eval skeleton, not a §1.6 safety guard).
