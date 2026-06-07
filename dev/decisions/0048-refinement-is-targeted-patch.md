# 0048 — Refinement is a targeted patch, not blind re-extraction (typed findings in, patch out)

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `architecture.md §1.7` (rewritten), `agents.md §5.4`/`§5.5`,
`pipeline.md §3.2.12`, `schema.md §4.9` (new "Refinement addressing" subsection),
`CLAUDE.md` (typed-boundary principle). Upholds the `architecture.md §1.5` LLM/framework
split. This is item **A1** (with its prerequisite **A2**) of the A1–G1 design-alignment plan.

## Context

The docs *gestured* at targeted refinement but never mandated a mechanism, and the prose
that the code read most literally pointed the wrong way:

- `architecture.md §1.7` framed refinement as "re-runs the responsible agent with structured
  feedback" — which reads as a full agent re-run. `agents.md` said the Extractor "re-runs with
  targeted feedback identifying the specific fields," but stopped short of a partial/patch emit.
- The code took the literal reading: refinement re-extracts the **whole** AttackSpec each pass.
  Re-rolling every field is non-convergent — an unflagged field can regress on any pass (the
  quality score bounces 9→6→9→10) — and costs ~10× per iteration versus editing the few flagged
  fields.
- `RefinementFeedback` carried a stringly-typed payload (`list[str]`), so field paths reached the
  agent as opaque prose. You cannot target fields programmatically from text — **A2** (typed
  *contents*, not a typed wrapper around stringified data) is the load-bearing prerequisite that
  makes patching possible at all.

The schema and jury already produce the surgical payload (per-field feedback with a field path
and a suggested fix); the design just wasn't spending it.

## Decision

**A1 — targeted patch is the convergent default.** On a validator finding or a jury `revise`,
the framework passes the responsible agent the **prior artifact** plus the **structured
findings** (`JuryFieldFeedback{field_path, problem, suggested_fix}` and/or
`StaticSchemaFinding{code, location, detail}`). The agent returns a **patch** — new
`{value, source, citations, …}` for **only** the named field paths. The framework deep-sets the
patch onto a copy of the prior artifact and re-validates (`AttackSpec.model_validate`). Unflagged
fields are byte-identical, so refinement is **convergent by construction**: a patch cannot regress
a field nobody flagged. Full from-scratch re-extraction is reserved for the artifact-level
natural-language-feedback path in interactive mode, where the user rejects the whole artifact
rather than naming fields.

**A2 — typed boundary means typed contents.** Structured findings travel between stages in their
structured form; they are rendered to prompt text *only* at the prompt boundary, with the
structured form retained for the framework. This is what lets the coordinator address and patch by
field path.

**Snapshots demoted to a secondary safety net.** `pipeline.md §3.2.12` best-state retention is no
longer the primary convergence mechanism (patches make intra-artifact regression impossible). It
remains a fallback that bounds *cross-phase* oscillation and preserves the best result on budget
exhaustion.

**Inline provenance is preserved by patching.** A patched field carries the agent's new
provenance; an untouched field keeps its original `source` and citations. A1 therefore does **not**
require the provenance side-map / chunked-emit redesign — that (D1/D2) is explicitly deferred.

## Consequences

- **Docs updated in this pass** (the contract now describes the better design): `architecture.md
  §1.7` and its retry-vs-refinement table; `agents.md` Extractor note (§5.4), jury `revise`
  contract and individual-field failure mode (§5.5); `pipeline.md §3.2.12` (patch is the default;
  snapshots reframed as secondary); `schema.md §4.9` new "Refinement addressing: field paths and
  patches" subsection; `CLAUDE.md` typed-boundary principle sharpened to "typed contents."
- **Code is a separate, later work-stream** (per "code follows the settled docs"): `RefinementFeedback`
  must carry typed findings rather than `list[str]`; the refinement coordinator must deep-set a
  field-path patch and re-validate; the Extractor/Planner refinement call must emit a patch, not a
  full artifact. None of that is done here.
- This establishes a **single accumulating structured-findings list** to patch from, which the
  downstream alignment items build on — especially A3/B1 (one orchestrator-owned mechanical
  validator stack feeding one findings set) and C1 (enrichment ordering).
- D1/D2 (provenance side-map, chunked emit) deferred; revisit only if truncation/cost keep biting
  after A1 lands in code.
