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

eval:
    uv run python -m eval.runner.cli

docs:
    @echo "Doc preview not yet implemented."
