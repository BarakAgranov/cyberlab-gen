# 0001 — typer over click

**Date:** 2026-05-17
**Phase:** Phase 0 (Task 0 setup)
**Architecture refs:** `docs/coding-conventions.md §11`, `docs/architecture.md §2.1`

## Decision

Use `typer` as the CLI framework for cyberlab-gen.

## Context

`coding-conventions.md §11` lists `click` vs. `typer` as a deferred decision
to be made at first use, with `typer` named as the recommendation. The four
CLI verbs (`generate`, `validate`, `fix`, `telemetry submit`) land as stubs in
Phase 0 Task 7; the framework choice must be locked before that.

## Alternatives considered

- **click** — mature, ubiquitous, explicit. Rejected because `typer` builds on
  click and generates the CLI surface from type hints. Given pyright-strict
  discipline across the project, deriving CLI signatures from annotations
  reduces duplication between the type system and the CLI argument parser.
- **argparse** — stdlib, no external dep. Rejected because the project already
  pays the cost of a CLI framework (subcommands, rich output integration, help
  text), and rolling those over argparse is unnecessary boilerplate.

## Consequences

- The `cyberlab_gen/cli/` subpackage uses typer's `Typer()` app pattern.
- Help text and option declarations live in function signatures.
- Future CLI additions (subcommands, options) follow the typer idiom.
