# 0044 — The propose → approve → overlay-write → validate loop (provisional resolution)

**Date:** 2026-06-07
**Phase:** 1 (operational hardening — the Wiz-CodeBuild `--auto` halt)
**Architecture refs:** `schema.md §4.16` (proposal lifecycle, overlay file shape,
per-run cap), `validation.md §6.4`/§6.10 (Layer 1 reference resolution, retry routing),
`pipeline.md §3.2.5` (post-Extractor interrupt, per-proposal review), `architecture.md
§1.5`/§1.6 (framework routes/writes; LLMs never do). Investigated in the Wiz-CodeBuild
report (this session). Follows ADR 0021 (proposals as a side-channel) and ADR 0043
(proposal rejections non-fatal).

## Context

A real `--auto extract` run on the Wiz CodeBuild blog halted with `halted_validation`
on `unknown_facet` (and other) findings even though the Extractor had *correctly*
proposed the missing facets. Investigation found:

1. The `propose_*` tools only appended proposals to in-memory lists; "acceptance" in
   both `--auto` and `--interactive` merely **printed** "auto-accepted N proposals into
   the overlay" and wrote nothing. `~/.cyberlab-gen/registry-overlay/` was never
   created. The propose → approve → overlay-write loop was **never implemented** (ADR
   0043 did not remove it).
2. A structural **ordering deadlock**: Layer 1 runs *before* the post-Extractor
   interrupt where acceptance happens (`pipeline.md §3.3` stage order). A spec failing
   Layer 1 on an unknown facet halts before the facet could ever be written — so even a
   correct proposal could never resolve the failure that triggered it.

## Decision

Implement the loop for the overlay-extensible vocabularies (`value_types`, `facets`;
`thesis_types` added in ADR 0045; `execution_contexts` is the Planner's, Phase 2).
`external_data_sources` stays maintainer-PR-only (`schema.md §4.16` line 744 — needs
adapter code, not just a registry row).

1. **Overlay writer** (`cyberlab_gen/registries/overlay_writer.py`) — mechanical
   framework code. Ensures the overlay dir, loads the existing per-vocabulary overlay
   file (or empty), appends/replaces the `entries:` row, records a framework-stamped
   `ProposalAuditBlock` in `proposals:`, and writes **atomically** (sibling `.tmp` +
   `Path.replace`). The acceptance coordinator
   (`cyberlab_gen/framework/proposal_acceptance.py`) converts each `Proposed*` to its
   entry (`Proposed*.to_entry`, stamping `proposed_by='extractor'`) and drives the writer.

2. **Provisional resolution at Layer 1** (the lighter ordering fix, preferred over
   rebuilding the frozen `MergedRegistries` mid-graph). `StaticSchemaValidator.validate`
   gains `pending: PendingProposals`. A facet/thesis-type reference absent from the
   registry but named in `pending` is a **provisional pass** (logged, not a finding), so
   the proposal survives Layer 1 to the acceptance point. The orchestrator builds
   `pending` from `state.extraction`; the eval runner's measurement re-validation applies
   the same set so it never records a false Layer-1 failure for a shipped spec. After
   acceptance writes the overlay, the term is durably resolvable next run and for
   `cyberlab-gen validate`.

3. **`--auto`** writes accepted proposals to the overlay up to
   `DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP` (5), marked `approval='auto'`. Over the cap the run
   **halts** with `ProposalCapExceeded` (`schema.md §4.16` option (c)) — a clear report
   for inspection, **not** a silent drop; no overlay entry and no `attack-spec.yaml` are
   written on that halt.

4. **`--interactive`** writes each Accepted (or Edited-then-revalidated) proposal via the
   same coordinator, marked `approval='human'`. No cap — the user acts on each.

5. **Audit-block schema changes** (additive; `ProposalAuditBlock`):
   - `source_lab` made **optional** (`None`). There is no lab at extraction time (the lab
     is the Planner's Phase-2 product), so an Extractor-stage proposal records no lab id;
     a Planner-stage proposal fills it in. Inventing a fake lab id, or repurposing the
     run id as a lab id, was rejected as a silent contract bend.
   - new **`approval: Literal["auto","human"]`** so telemetry-driven overlay→bundled
     promotion (`schema.md §4.16` step 4) can weight human-approved entries. Framework-
     authored, never agent-authored.

## Judgment calls (not pinned by the docs)

- **`source_lab` optional vs. run-id reuse** → optional (above).
- **Eval does not write the global overlay.** The eval runner drives the pipeline (which
  provisionally resolves in-run) and re-validates for the metric with the same `pending`,
  but it does **not** call the `extract`-verb accept path, so a measurement run never
  mutates the user's global `~/.cyberlab-gen` overlay. Measurement stays read-only w.r.t.
  global registry state.
- **Over-cap = halt, writing nothing** (vs. write-up-to-cap-then-ship). Chosen to match
  "halted with a clear report for user inspection" and to keep the overlay untouched on
  the halt; the user re-runs `--interactive` to review each.

## Consequences

- New module `registries/overlay_writer.py`, new module
  `framework/proposal_acceptance.py`, new `PendingProposals` on the validator, new
  `ProposalCapExceeded` error, `overlay_dir` param threaded through `run_extract`.
- `--auto`/`--interactive` now genuinely populate `~/.cyberlab-gen/registry-overlay/`.
- A spec whose only Layer-1 problem is an as-yet-unregistered term the Extractor proposed
  now ships (provisional pass) instead of burning the structural-retry budget and halting
  — the exact Wiz-CodeBuild failure.
- `external_data_sources` and (pre-ADR-0045) `thesis_types` remain non-proposable, so a
  spec referencing an unknown one still halts; ADR 0045 opens `thesis_types`.
