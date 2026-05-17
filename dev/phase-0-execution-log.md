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
