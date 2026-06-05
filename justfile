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

# Run the eval. Pass through flags, e.g. `just eval --blog <id>` for one blog.
eval *ARGS:
    uv run python -m eval.runner.cli {{ARGS}}

docs:
    @echo "Doc preview not yet implemented."
