# ADR 0012: LocalState path resolution — hardcoded `~/.cyberlab-gen`, not `platformdirs`

**Status:** Accepted (Phase 0 Task 6)
**Date:** 2026-05-18
**Decider:** Task 6 implementation agent

## Context

Phase 0 Task 6's brief (`dev/phase-briefs/phase-0-agent-brief.md`) instructs:

> Use `platformdirs` so the paths are correct on macOS, Linux, and Windows.

However:

1. **`docs/architecture.md §2.3`** (system diagram, lines 348–351) specifies
   the local-state root as `~/.cyberlab-gen/` literally, on every platform.
   The diagram is unambiguous: `config.yaml | cache/ | checkpoints/ | runs/
   | reports/` all sit directly under `~/.cyberlab-gen/`.

2. **`docs/architecture.md §2.2`** (line 293) specifies the registry overlay
   path as `~/.cyberlab-gen/registry-overlay/`. Other doc references
   (`schema.md §4.11`, `pipeline.md §3.2.1`, `§3.6`, `§3.7`,
   `provider-interface.md §5.1`, `§5.2`) all use the same literal root.

3. **Existing code** (`cyberlab_gen/registries/loader.py:108`'s
   `default_overlay_dir()`) hardcodes `Path.home() / ".cyberlab-gen" /
   "registry-overlay"`. The docstring explicitly notes that Task 6's
   `LocalState` will own this resolver — i.e., the prior author assumed
   consistency with the literal-string path.

4. **The CLAUDE.md authority gradient** states: "architecture.md > other
   `docs/*.md` > this file > `dev/decisions/`. If two sources conflict,
   defer up the chain and record the conflict." Per-task briefs sit in
   `dev/` and are below `dev/decisions/` in this gradient. Architecture wins.

## Decision

`LocalState` uses `Path.home() / ".cyberlab-gen"` as the literal root on
every platform. `platformdirs` is **not** used.

`registries/loader.py:default_overlay_dir()` is refactored to delegate to
`LocalState().registry_overlay_dir`, eliminating the duplicated literal
path string.

## Consequences

- On Windows, the local state lives at `C:\Users\<user>\.cyberlab-gen\`.
  On macOS at `/Users/<user>/.cyberlab-gen/`. On Linux at
  `/home/<user>/.cyberlab-gen/`. All three are the literal
  hidden-dotfolder pattern, **not** the platform-correct
  `%LOCALAPPDATA%\cyberlab-gen\` (Windows) or
  `~/Library/Application Support/cyberlab-gen/` (macOS) that
  `platformdirs` would produce.

- Consistency with `registries/loader.py:default_overlay_dir()` is
  preserved; both paths share a single source of truth (`LocalState`).

- `platformdirs>=4` remains a project dependency (declared in
  `pyproject.toml`) but is unused by Phase-0 code. A Phase-1+ housekeeping
  sweep can remove the dependency if no later code needs it; alternatively,
  future code may use it for non-state purposes.

- If the architecture is ever revised to specify platform-correct paths
  (`~/Library/Application Support/...` on macOS, etc.), the migration
  must update `LocalState`, `default_overlay_dir()`, **and** every doc
  reference to `~/.cyberlab-gen/` together. This ADR documents the
  current single point of truth.

## Brief revision recommended

The next Phase-0-brief sweep (or the Phase-1 brief that supersedes Task 6's
config-fields stub) should drop the "use `platformdirs`" instruction or
explicitly cite this ADR alongside.

## References

- `docs/architecture.md §2.2`, `§2.3`
- `dev/phase-briefs/phase-0-agent-brief.md` Task 6 block
- `cyberlab_gen/registries/loader.py:default_overlay_dir`
- `CLAUDE.md` authority gradient
