# 0108 — Descriptive names for every layer taxonomy, everywhere (code + docs)

**Date:** 2026-06-21
**Phase:** 2 (naming-discipline reconciliation)
**Architecture refs:** `coding-conventions.md §5.5` (the descriptive-name rule), ADR 0026
(descriptive validator symbols), ADR 0046 (`static_schema` everywhere — **its docs-taxonomy-preserved
scope boundary is superseded here**), `validation.md §6` (the validation-layer contract), ADR 0056
(the iteration caps). Maintainer-approved this session (name table + docs approach + scope).

## Context

`coding-conventions.md §5.5` requires every numbered/sequenced construct to take a *meaningful,
descriptive name* in code; the bare ordinal ("Layer 2", "L3") is a documentation-side slot reference
only, and the token (`layer2`/`l2`/`L2`/…) must not appear in any code identifier. ADR 0026 + 0046
applied this to **Layer 1** (`static_schema` everywhere) but ADR 0046 **deliberately preserved** the
docs positional taxonomy "Layer 1/2/3/5" and left the other layers for later.

Two regressions / gaps surfaced (the run-20260621 plan-eval + an architect review):

1. **The eval harness re-introduced the ordinal token for Layer 2.** `PlanRunRecord.layer2_passed`,
   `PlanBlogAggregate.layer2_pass_rate`, `PlanEvalReport.overall_layer2_pass_rate()`, the
   `progress.py` console `layer2=` string, the `cli.py` "layer-2 pass rate" string, tests, and a
   `CALIBRATION.md` reference — all carry `layer2`, exactly the anti-pattern ADR 0046 removed for
   `layer1`. (The pipeline itself is clean: `SemanticCrossCheckValidator`, status
   `HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED`, node id `"semantic_cross_check"`.)
2. **The docs still lead with the ordinal**, and code comments use an informal, sometimes
   inconsistent retry-shorthand "L1–L5" — opaque without a legend.

There are in fact **two distinct layer taxonomies** that collide on the letter "L":

- **A. Validation layers** (`validation.md §6.4–6.8`) — a real contract.
- **B. Retry/iteration caps** — informal "L1–L5" shorthand in comments (`architecture.md §6`, ADRs
  0018/0023/0056): per-node structural retry / per-agent refinement / global iteration cap (+
  recursion-limit backstop) / checkpoint persistence.

## Decision

Adopt **descriptive names as the primary label everywhere — code identifiers, report/metric keys,
user-facing strings, comments, and docs prose**. The ordinal **number** survives only as an explicit
*ordering annotation* where the cheap→expensive order or the stable report slot matters; it is never
the working label.

### Taxonomy A — validation layers (canonical names)

| # (ordering only) | descriptive name | code token / report key |
|---|---|---|
| 1 | static-schema validation | `static_schema` (done — ADR 0026/0046) |
| 2 | semantic cross-check | `semantic_cross_check` |
| 3 | containerized dry-run | `containerized_dry_run` |
| 4 | real-platform apply (v2-deferred) | `real_platform_apply` |
| 5 | safety scans | `safety_scan` |

The number is retained **only** as an ordering/slot annotation (the passes run cheap→expensive:
static-schema → semantic cross-check → containerized dry-run → [real-platform apply, v2] → safety
scans; the deferred-Layer-4 slot stays reserved so v2 adds it without renumbering). Descriptive report
keys *improve* slot stability — adding `real_platform_apply` later shifts no other key. `validation.md`
keeps one historical old-number→name note for continuity.

### Taxonomy B — retry/iteration caps (spell out in place)

The "L1–L5" comment shorthand is replaced by the mechanism's name at each site: **per-node structural
retry**, **per-agent refinement budget**, **global iteration cap** (with its **recursion-limit
backstop**), **checkpoint persistence**. No rigid number table — each comment names what it means.

### Eval-code rename (the concrete Layer-2 violation)

`layer2_passed` → `semantic_cross_check_passed`; `layer2_pass_rate` → `semantic_cross_check_pass_rate`;
`overall_layer2_pass_rate` → `overall_semantic_cross_check_pass_rate`; the `progress.py` console label
`layer2=` → `cross_check=`; the `cli.py` "layer-2 pass rate" → "semantic cross-check pass rate"; tests
and the `CALIBRATION.md` symbol reference updated to match.

## Scope boundaries

- **Superseded:** ADR 0046's "keep the docs positional taxonomy / report-key examples numbered"
  decision. Its symbol renames stand; the descriptive-name discipline now reaches the docs too.
- **Left as historical record (not edited):** `dev/` execution logs, prior ADRs (0016/0022/0026/0046
  and others), and archived eval reports — the same call ADR 0046 made for `layer1`.
- **Section numbers unchanged:** `validation.md §6.4` etc. stay (they are section ids, not layer
  ordinals), so cross-references keep resolving.

## Consequences

- One naming convention, applied uniformly: a reader sees "semantic cross-check", never "Layer 2".
- Archived plan-eval reports don't round-trip into the renamed eval models (the ADR-0046 call again;
  a handful of historical files, not re-run).
- `coding-conventions.md §5.5`'s precedent block is extended from Layers 1–2 to all five.
- `just verify` green after the sweep.
