# 0002 — hatchling as the build backend

**Date:** 2026-05-17
**Phase:** Phase 0 (Task 0 setup)
**Architecture refs:** `docs/coding-conventions.md §2.1`, Task 0 brief

## Decision

Use `hatchling` (PEP 517 backend) as the build system for cyberlab-gen.

## Context

The Task 0 brief gives the implementer discretion among hatchling, setuptools,
and flit, requiring only that the choice be documented.

## Alternatives considered

- **setuptools** — universal, mature, the historical default. Rejected because
  setuptools' configuration model leans on legacy `setup.py`/`setup.cfg`
  conventions; `pyproject.toml`-native setups feel grafted on. The project has
  no setuptools-specific feature dependency.
- **flit** — minimalist, PEP 621-native. Rejected because hatchling has a
  broader feature set (versioning, plugin system, multi-target builds) we may
  need by Phase 6 release without adding migration cost now.
- **poetry-core** — Rejected because the project uses `uv` for environment
  management; introducing poetry's build backend creates two competing tooling
  philosophies in one repo.

## Consequences

- `[build-system]` in `pyproject.toml` declares `hatchling` and
  `hatchling.build`.
- Wheel packaging configured under `[tool.hatch.build.targets.wheel]`.
- Future build-time hooks (e.g., embedding registry resources into the wheel)
  use hatchling's plugin system.
