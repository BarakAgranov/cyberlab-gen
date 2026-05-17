# Phase 0 execution log

A running record of what each Phase 0 task actually built, what surprised the
implementer, and what was deferred. Entries are append-only; each task's
implementer adds an entry at the end.

The purpose is to inform Phase 1's brief and Phase 1's implementers: where
were the docs ambiguous? what design calls came up that the brief didn't
anticipate? what was harder or easier than expected?

Keep entries terse. Two paragraphs per task is usually right; a long entry
suggests something worth promoting into a `dev/decisions/` ADR instead.

---

## Task 0: Setup

**Date:** 2026-05-17
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~6 minutes execution (plan-mode work preceded; not counted)
**Commit:** `cdfa8261a9c4d8a77ace9dd963d8ece725da5f21`

### What was built

The eight `cyberlab_gen/` subpackages with docstring-only `__init__.py` files
(each docstring names the architectural section that governs the subpackage),
the `tests/{unit,integration,eval}/` layout with a one-test smoke file, the
`registry/` and `eval/{blog-sets,runner,reports}/` placeholder directories,
the tooling baseline (`pyproject.toml` with Phase 0 deps minus `openai` and
the three deferred `pytest-*` plugins, `uv.lock`, `justfile`, `.python-version`
pinned to 3.13, `.gitignore` extensions for venv/coverage/pyright), and the
GitHub Actions CI workflow on a Python 3.13/3.14 matrix. ADRs 0001 (typer),
0002 (hatchling), 0003 (Python upper bound `<3.15` with matrix) committed.
Local verification: ruff check, ruff format --check, pyright strict, pytest
all green.

### Surprises and friction

- **`just` not installed** on the dev machine (Windows). Ran the four gates
  directly via `uv run` instead. CI installs `just` via `extractions/setup-just@v4`
  and runs `just verify` end-to-end, so the gate is enforced in CI even when
  not locally.
- **Hatchling refused to build** because `pyproject.toml` declared `readme = "README.md"`
  but the file doesn't exist yet (Task 9 deliverable). Resolved by removing
  the `readme` line; Task 9 will re-add it together with `README.md` itself.
- **Ruff RUF002** caught an en-dash (`–`) in the `schemas/__init__.py` docstring
  ("Tasks 1–3"). Replaced with an ASCII hyphen. Worth knowing for Phase 1
  prompt-writing — avoid Unicode dashes in code.
- **Pyright doesn't honor `# noqa: F401`** — the original `import cyberlab_gen  # noqa: F401`
  in the smoke test still tripped `reportUnusedImport`. Rewrote the test to
  actually use the imported name (`assert cyberlab_gen.__name__ == "cyberlab_gen"`),
  which is also a stronger test.
- **`astral-sh/setup-uv` no longer publishes minor tags.** The plan's `@v3`
  reference was already known to need verification; web search confirmed the
  current best practice is pinning to an immutable patch tag. CI now uses
  `astral-sh/setup-uv@v8.1.0`.

### Deferred to later phases

- `README.md` and CONTRIBUTING.md (Task 9). The `readme` line in
  `pyproject.toml` will be re-added then.
- `cyberlab-gen` console_script entry-point in `pyproject.toml` (Task 7, when
  `cyberlab_gen.cli.main:main` exists).
- `pytest-cov`, `pytest-recording`, `pytest-asyncio` dev-deps (Phase 1+ when
  first exercised).
- `openai` SDK runtime dep (Phase 1+ when the OpenAI adapter is written).
- `tests/cassettes/` and any VCR plumbing (Phase 1+).
- Quote-style ADR (default `"double"` in `pyproject.toml` is the documentation).

### Doc-improvement notes for the next brief writer

Surface these to the architect as separate doc edits (not part of Task 0):

1. **`coding-conventions.md §1.1`'s `<3.14` Python cap is stale.** ADR 0003
   supersedes it for implementation; §1.1's literal cap value needs updating
   (the cap principle still holds — only the value is wrong).
2. **`coding-conventions.md §10.2` lists `openai` as a Phase 0 dep**, which
   conflicts with §10.1's just-in-time principle and with Tasks 5a/5b's
   "`<pinned-in-release>` placeholders" framing. Suggest moving `openai` to
   Phase 1 in §10.2.
3. **`coding-conventions.md §2.4` describes a testing stack** (`pytest-cov`,
   `pytest-recording`, `pytest-asyncio`) that doesn't apply to Phase 0. The
   doc could clarify which testing deps belong to which phase, matching the
   §10.2 pattern.
4. The phase-0 brief's Task 0 step 2 ("Move the architecture documents into
   `docs/`") is obsolete — docs were already in `docs/` at repo init.
5. The phase-0 brief uses `requires-python = ">=3.13"` whereas conventions §1.1
   uses `>=3.13,<3.14` and ADR 0003 now uses `>=3.13,<3.15`. The brief should
   pull the value from conventions rather than restate it.

---

## Task 1: Pydantic schema base layer

**Date:** 2026-05-17
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~15 minutes execution (plan-mode work preceded; not counted)
**Commit:** `29f68b5`

### What was built

`cyberlab_gen/schemas/base.py` (the `ArtifactModel` / `InternalModel` pair from
`schema-details.md §1`), `cyberlab_gen/schemas/primitives.py` (the seven
constrained-string aliases from §2.1 plus a `pydantic.HttpUrl` re-export), and
`cyberlab_gen/schemas/enums.py` (every closed `StrEnum` from §2.2, 22 enums
total, each retaining its `schema.md §X.Y` docstring citation per the schema-
details convention). `cyberlab_gen/schemas/__init__.py` now re-exports the
whole surface via an explicit `__all__`. Tests live under
`tests/unit/schemas/` split per source file: `test_base.py` (5 tests),
`test_primitives.py` (19 tests covering one-accept-one-reject per primitive),
`test_enums.py` (23 tests pinning every enum's full value set plus a Severity
round-trip through `ArtifactModel`). 48 tests pass; ruff, format-check, and
pyright strict all clean.

### Surprises and friction

- **Pyright's `type` is not iterable.** The first cut of `test_enums.py` had
  `def _values(enum_cls: type) -> set[str]` which pyright strict rejected
  (`"type" is not iterable`). Annotating as `type[StrEnum]` fixed it and
  is the stronger contract anyway.
- **The brief's enum example list includes `MessageRole`**, which lives in
  `provider-interface.md §4.1` (Task 5a), not in `schema-details.md §2.2`.
  Per CLAUDE.md's authority gradient (architecture > brief), `MessageRole`
  was omitted from `schemas/enums.py`; see the doc-improvement note below.
- No friction with PEP 695 syntax (none used here — generics land in Task 2's
  `Provenance[T]`).
- The architecture's choice of `use_enum_values=False` (`schema-details.md §1`)
  means `model_dump()` returns enum *members*, not their string values. The
  Severity round-trip test pins this so a future change to the config would
  fail loudly.

### Deferred to later phases

- `Provenance[T]`, `CitationBlock`, source-rules validator — Task 2.
- `to_yaml()` / `from_yaml()` artifact methods — Task 2 (first round-trip
  consumer is `AttackSpec`).
- Open-set string types (`ExecutionContext`, `ValueTypeName`, etc.) and their
  registry validators — Task 4.
- `MessageRole` enum — Task 5a (`providers/base.py`); see below.

### Doc-improvement notes for the next brief writer

1. **Task 1's `Required reading > Primary` mention of `MessageRole`** in the
   enum example list is cross-package. `MessageRole` is defined in
   `provider-interface.md §4.1` and owned by Task 5a (`providers/base.py`).
   Either replace `MessageRole` with another `schema-details.md §2.2`
   example (e.g., `CitationKind`) or note explicitly that the example was
   illustrative across subpackages.
2. **`schema-details.md §2.1` shows the constrained primitives living in
   `cyberlab_gen/schemas/common.py`** (line 58 of the doc) while the
   phase-0 brief specifies `primitives.py`. The brief wins for filename
   (decision-discretion); worth aligning the doc's filename comment so
   readers don't infer a second module name.

---
