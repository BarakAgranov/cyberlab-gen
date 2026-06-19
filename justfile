set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]
# justfile for cyberlab-gen

default:
    @just --list

sync:
    uv sync --all-extras

verify: lint-check format-check type test

lint-check:
    uv run ruff check .

format-check:
    uv run ruff format --check .

fmt:
    uv run ruff format .

lint:
    uv run ruff check --fix .

type:
    uv run pyright

test:
    uv run pytest

# Run the eval. Pass through flags, e.g. `just eval --blog <id>` for one blog, or
# `just eval --stage plan` for the Phase-2 plan stage (default stage is extract).
eval *ARGS:
    uv run python -m eval.runner.cli {{ARGS}}

# Convenience alias for the plan-stage eval (ADR 0102). Extra flags pass through,
# e.g. `just eval-plan --blog <id>`.
eval-plan *ARGS:
    uv run python -m eval.runner.cli --stage plan {{ARGS}}

docs:
    @echo "Doc preview not yet implemented."
