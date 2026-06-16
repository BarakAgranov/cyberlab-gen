# 0083 — Reconcile coding conventions with established practice (PEP 563 annotations; cross-subpackage imports)

**Date:** 2026-06-16
**Phase:** 2 (audit follow-up — convention-vs-code drift)
**Conventions refs:** `coding-conventions.md §1.2` (no `from __future__`), `§3.1` (cross-subpackage imports via `__init__`), `§3.3` (no cycles). `CLAUDE.md` mirrors §3.1.

## Context

An engineering audit (2026-06-16) found two `coding-conventions.md` rules contradicted by pervasive, green, reviewed code. In both cases the code embodies a deliberate, working practice and the *convention text* is the stale party (the same shape as the reproducibility / ADR-0081 case: the contract, not the implementation, was wrong). These rules are agent-facing — a future agent that trusts the stale text would either "fix" working code or be misled — so they are reconciled here rather than left as silent drift.

- **CONV-2 — `from __future__ import annotations`.** `§1.2` reads "no `from __future__` imports, no `six`, no `2to3`." That list is framed against **Python-2 compatibility shims**; `from __future__ import annotations` (PEP 563, deferred annotation evaluation) is a Python-3 feature, not a Py2 shim. It appears in 31 files and is **load-bearing**: the codebase imports many types only under `if TYPE_CHECKING:` and then uses those names in **eagerly-evaluated annotation positions** — dataclass fields (`framework/enrichment.py` `EnrichmentConfig.mitre_catalog: MitreTechniqueCatalog`) and function/method signatures (`validators/static_schema_validator.py` `registries: MergedRegistries`; `state/run_store.py` `directory: Path`). Without PEP 563 those annotations evaluate at definition time and raise `NameError` on the `TYPE_CHECKING`-only names; PEP 563 turns them into un-evaluated strings. This is a generic Python forward-reference fact, and it also satisfies ruff's `TC` rules. The blanket ban never intended to forbid this. **(Correction, 2026-06-16 — see below: the original draft of this bullet mis-attributed the requirement to LangGraph's `get_type_hints`; that reason is wrong.)**

- **CONV-1 — cross-subpackage imports.** `§3.1` reads "Internal modules are not imported across subpackage boundaries except through the `__init__.py` re-export." A scan finds ~138 cross-subpackage leaf-module imports (e.g. `from cyberlab_gen.schemas.attack_spec import AttackSpec` from `framework/`). The whole codebase — and every test — imports this way; the rule is followed only for the *stable public surfaces* that warrant it (`agents/__init__` for the Task-3/5 call surface; the package roots). The rule as literally stated describes a practice the project never adopted.

## Decision

Amend the conventions to the practice the code already embodies. **No code changes.**

1. **PEP 563 is permitted (CONV-2).** `§1.2`'s ban is scoped to Python-2 compatibility (`six`, `2to3`, the legacy `__future__` flags). `from __future__ import annotations` is explicitly allowed, and is required wherever a `TYPE_CHECKING`-only name appears in an eagerly-evaluated annotation position (a dataclass field or a function/method signature) and to satisfy ruff's `TC` rules.

2. **The `__init__` re-export is the *stable public surface*, not a blanket import gate (CONV-1).** Each subpackage's `__init__.py` still re-exports its public surface, and cross-phase / external consumers (and tests of the public API) should import from there. Direct leaf-module imports across subpackages are acceptable for internal wiring and are the norm. The hard structural constraint is the one that actually carries the weight: **no import cycles** (`§3.3`, unchanged) — cycle-prevention was always enforced by the explicit cycle ban, not by routing every import through `__init__`.

## Alternatives considered

- **Enforce CONV-1** (an import-linter rule banning cross-subpackage leaf imports + rewrite ~138 sites) — rejected: large churn that fights idiomatic Python, would force every `__init__` to re-export nearly everything (increasing coupling-to-`__init__` and cycle risk), for marginal benefit over the explicit cycle ban. The genuinely valuable part — a curated public surface per subpackage — is kept.
- **Strip the 31 `from __future__ import annotations`** — rejected: they are load-bearing (TYPE_CHECKING-only names in eagerly-evaluated dataclass-field / signature positions) and satisfy ruff `TC`; removing them naively raises `NameError` at import (empirically, see the Correction). Making them *genuinely* droppable would mean promoting those TYPE_CHECKING names to runtime imports — churn for zero behavior change — so the import is kept as-is.
- **Leave the drift** — rejected: agent-facing conventions that contradict the code mislead future contributors (and agents), the exact failure mode this audit exists to close.

## Consequences

- `coding-conventions.md §1.2` and `§3.1` amended; `§3.3` unchanged (it already carries the cycle ban). `CLAUDE.md`'s mirror of the §3.1 rule updated to match.
- No production or test code changes; `just verify` stays green by construction (docs-only).
- Future agents reading the conventions see the actual policy; the public-surface `__init__` re-exports remain the recommended import path for cross-phase consumers.
- Reversible: if the architect later wants strict enforcement, this ADR is superseded and the import-linter path (alternative 1) is taken.

## Correction (2026-06-16 — empirical strip-test)

The original draft justified CONV-2 with "load-bearing for LangGraph's runtime
`get_type_hints`." **That causation is backwards and is hereby corrected** (the *decision* —
PEP 563 permitted, import retained — is unchanged; only the *reason* was wrong).

`get_type_hints` must resolve a referenced type at runtime **with or without** the
future-import — PEP 563 defers *when* an annotation is evaluated, not *whether* its names must
exist when `get_type_hints` resolves it. So the future-import does not make `get_type_hints`
work; if anything it adds a re-eval step. (`PipelineState`'s field types — `ExtractionResult`,
`AttackSpec`, … — are deliberately *runtime* imports precisely so `get_type_hints` can resolve
them, and that requirement is identical either way.)

Empirical check (ADR 0085 work, strip a 5-file sample incl. `orchestrator.py` on `main`, run
`uv run python -c "import …"` + pyright, then `git checkout` to revert):

- Removing `from __future__ import annotations` raised `NameError` at **import** (class /
  function definition time), on `TYPE_CHECKING`-only names used in eagerly-evaluated positions:
  `MitreTechniqueCatalog` (dataclass field `EnrichmentConfig.mitre_catalog`),
  `MergedRegistries` (method param in `static_schema_validator`), `Path` (param in
  `run_store` — `Path` *is* `TYPE_CHECKING`-imported there, so it breaks too).
- pyright (PEP-563-aware) flags the same undefined names — it agrees with the runtime
  breakage rather than being "happy."
- `get_type_hints(PipelineState)` was **not** the failing path.

So the future-import is load-bearing **as the code is currently written** (the generic
forward-reference fact above), not because of any LangGraph behavior. The
`orchestrator.py` lines 49-53 comment was corrected to match.
