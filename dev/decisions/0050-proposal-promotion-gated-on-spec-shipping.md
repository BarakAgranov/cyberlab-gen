# 0050 — Proposal promotion to the global overlay is gated on the spec shipping

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `schema.md §4.16` (rewritten proposal lifecycle + per-run cap),
`agents.md §5.7` (Planner-Jury), `registry-details.md` (overlay metadata). Builds on / amends
ADR 0044 (propose → approve → overlay → validate loop) and ADR 0015/0016 (proposal shapes).
Upholds the `architecture.md §1.6` mechanical-safety rule and the `§1.5` LLM/framework split.
This is item **E1** of the A1–G1 design-alignment plan, with the maintainer's gate revision folded in.

## Context

Two problems with the documented proposal flow:

1. **`--auto` wrote every proposal straight to the shared global overlay with no gate.** This is
   the structural root of the registry pollution cleaned up earlier (the `example-com-blog`
   overlay entries — `target:fastly`, `s3_bucket_arn` with `proposed_by_model: m`) and of
   cross-run nondeterminism: a proposal from a run that never shipped a usable spec still mutated
   shared state that the *next* run reads ("worked yesterday, broke today").

2. **The originally-considered fix was a jury-review-of-proposals gate** (the jury judges each
   proposal for overlap / justification / shape). That makes an LLM do a mechanical job (overlap
   dedup) and adds a second, separate review channel parallel to the spec review the jury already
   does.

## Decision

Promotion to the global overlay is gated on **the spec shipping**, not on a separate
jury-proposal gate. The lifecycle:

1. **Propose** — agent emits `proposed_entry` after searching bundled **and** overlay.
2. **Provisional within-run resolution** — the term resolves in a **run-scoped** view so the spec
   can validate and proceed. **No global write.** Interactive per-proposal Accept/Edit and `--auto`
   auto-accept both operate only on this run-scoped view.
3. **The jury reviews the spec**, not the proposals in isolation. A proposal's justification is
   covered implicitly: the field that uses the term is reviewed like any other, and an unjustified
   term shows up as a provenance/fidelity problem fixed by the normal refinement patch (ADR 0048).
4. **Promotion to the global overlay — gated on the spec shipping.** If and only if the spec
   ships, its provisional terms are written to `~/.cyberlab-gen/registry-overlay/` with a
   **mechanical dedup/overlap merge-check at write time**. If the spec does not ship (jury
   `reject`, mechanical halt, user abort), nothing is promoted — the terms stay in the run record.
5. **Graduate to bundled** — overlay → bundled via maintainer PR informed by telemetry. Unchanged.

The mechanical merge-check **replaces** the jury-proposal-overlap gate (dedup is deterministic and
auditable, `§1.6`, not an LLM call). **Over-cap** (the per-run proposal cap) becomes **in-loop
steering** (use existing / refactor) **bounded by the refinement iteration/budget caps** (ADR 0049),
not a hard halt that short-circuits the loop.

## Rationale

- **A shipped spec's vocabulary is always globally resolvable** — every term a shipped lab
  references now exists in bundled or overlay; no dangling references to terms that exist in no
  registry.
- **No orphan overlay entries** — a term reaches the shared overlay only alongside a spec that used
  it *and* shipped.
- **`--auto` promotes only terms from specs that shipped** — the real guardrail, versus the old
  "write every proposal" bug.

## Consequences

- **Docs updated in this pass:** `schema.md §4.16` (5-stage lifecycle; per-run cap reframed to
  bounded in-loop steering), `agents.md §5.7` (Planner-Jury reviews the spec; Accept/Edit is the
  user's menu; dedup is the mechanical merge-check), `registry-details.md` (overlay metadata set
  on spec-ship, not at proposal time).
- **Code is a separate, later work-stream:** move the overlay write from proposal-acceptance time
  to spec-ship time; add the write-time merge-check; make `--auto` never write un-promoted
  proposals; make over-cap steer in-loop within the caps rather than hard-halt.
- **Flagged drift (not changed here, to avoid scope creep):** `schema.md §4.16` "Proposal
  authority by registry" still lists only `value_types` / `facets` / `external_data_sources`;
  `execution_contexts` and `thesis_types` are runtime-proposable per ADR 0044/0045 but absent from
  that list, and `registry-details.md` line ~16 still calls `thesis_types` PR-only. Surfaced for a
  follow-up pass — the promotion-gate rewrite does not depend on resolving it.
