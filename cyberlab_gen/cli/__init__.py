"""CLI subpackage — user-facing command-line entry points.

Exposes the four verbs (`generate`, `validate`, `fix`, `telemetry submit`) per
`docs/architecture.md §2.1`. User-facing output goes through `cli.output` per
`docs/coding-conventions.md §6.3`. Phase 0 ships docstring-only stubs; the
typer-based entry points land in Task 7 of `dev/phase-briefs/phase-0-agent-brief.md`.
"""
