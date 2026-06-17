# 0096 — `extract` / `plan` are permanent staged entry points, not transitional scaffolding

**Date:** 2026-06-17
**Phase:** 2 (Task 6 pre-work — a surfaced architectural question)
**Deciders:** maintainer (architect — raised the question), implementing agent (researched + documented)
**Architecture refs:** `architecture.md §2.1` (the CLI verb list — already states the both/and),
`§2.3` (the system diagram — lagged), `pipeline.md §3.1` (modes framed against `generate`),
`implementation-plan.md §3.1`/§7.1 + the phase table. Frames ADR 0013 (the original four-verb
scaffold) and ADR 0024 (the `extract` verb's full user surface).

## Context

The product spec's headline command is `cyberlab-gen generate <url>` — one pipeline, stages internal.
But Phase 1 shipped an `extract` verb and Phase 2 adds a `plan` verb (per-stage entry points). The
architect flagged the decode-debt risk: an undocumented "is this verb temporary?" is the same class of
debt as an undocumented "Layer 2" — a future reader/agent should not have to guess whether `extract` /
`plan` are scaffolding to be removed when `generate` ships. Resolve and document it before building the
`plan` verb (so the verb's help text and persistence framing reflect the settled answer).

## Decision

**`extract` and `plan` are permanent per-stage entry points that coexist with `generate`** (Framing 2
of three considered: intermediate-and-subsumed / permanent-and-coexisting / undocumented-drift).

This is **not a new decision** — `architecture.md §2.1` already states it directly: "The generation
pipeline runs end-to-end via `generate`, **or stage-by-stage via `extract` → `plan` → `generate`, each
consuming the prior stage's typed artifact**," and "`generate` runs it internally." `generate`
*composes* the same stages; it does not *replace* the staged verbs. The Phase-2 Task-0 reconciliation
called the `extract → plan → generate` staging "**locked**," and `implementation-plan.md §7.1` still
names that chain as the pipeline's standing description at Phase 4 (the latest phase). The staged verbs
are the natural affordance of the typed-artifact contract: because each stage emits a typed artifact
the next consumes (`AttackSpec` → `LabManifest`), a per-stage entry point lets the user inspect/edit the
intermediate artifact and resume — which the headline single-command framing composes, not contradicts.

**What this ADR adds is documentation, not a contract change:**
1. `CLAUDE.md` "Status right now" — a note that `extract`/`plan` are permanent staged entry points, not
   scaffolding (the place an agent reads "extract works, the rest are stubs" and might infer "extract is
   temporary").
2. `architecture.md §2.3` — a note under the system diagram explaining the staged entry points are
   omitted from the headline diagram **for space**, not because they are transitional (the diagram's CLI
   box shows only `generate`/`fix`/`validate`/`telemetry submit` — the one genuine doc lag behind §2.1).

## Consequences

- The "is this verb temporary?" decode-debt is closed for both human readers and future agents.
- The `plan` verb (Task 6) is built and documented as a first-class, permanent command — its run-store
  records, help text, and `lab.yaml` deliverable are not framed as throwaway.
- Doc-hygiene noted, not over-corrected: `pipeline.md §3.1` frames `--interactive`/`--auto` against
  `generate`, which is acceptable (`generate` is the headline path and the staged verbs carry their own
  modes per ADR 0013/0024); not churned here. The only real lag was the §2.3 diagram, now annotated.

## Alternatives considered

- **Framing 1 (intermediate, subsumed by `generate`).** Rejected: §2.1 says `generate` runs the stages
  *internally* (composition), and no doc/ADR deprecates or removes `extract`/`plan` once `generate`
  ships; the staged chain is still the pipeline's description at Phase 4.
- **Framing 3 (undocumented drift).** Rejected: the verbs trace to the implementation-plan phase table
  and Phase-0 scaffolding note, ADR 0024 (`extract`), and a deliberate, surfaced Task-0 reconciliation
  ("locked"). The only drift is the §2.3 diagram lagging §2.1's prose — a hygiene defect (now fixed),
  not a divergence in intent.
- **Reflow the §2.3 ASCII diagram to add the two verbs.** Rejected: six verbs overflow the box and risk
  mangling alignment for no semantic gain; a prose note under the diagram is clearer and lower-risk.
