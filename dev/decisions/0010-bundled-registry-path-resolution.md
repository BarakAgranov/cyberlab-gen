# 0010 — Bundled registry path resolution / wheel-packaging deferral

**Date:** 2026-05-18
**Phase:** Phase 0 (Task 4)
**Architecture refs:** `CLAUDE.md` project map, `docs/schema.md §4.11`, `pyproject.toml`

## Decision

Task 4's loader resolves the bundled registry directory at
`Path(cyberlab_gen.__file__).resolve().parent.parent / "registry"`.

The wheel-distribution case — i.e., what happens when `cyberlab-gen` is installed via `pip install` rather than run from the source tree — is left unresolved. Phase 0 has no published distribution, no wheel-install test, and no path-resolution failure mode that affects the Task-4 exit criteria.

## Context

`CLAUDE.md`'s project map lists `registry/` as a top-level sibling of `cyberlab_gen/`, not a subpackage of it. `pyproject.toml` has:

```toml
[tool.hatch.build.targets.wheel]
packages = ["cyberlab_gen"]
```

Only the `cyberlab_gen` package is shipped into wheels; `registry/` is NOT currently included. Therefore `importlib.resources.files("cyberlab_gen") / ...` cannot reach the bundled YAMLs, and a `pip install cyberlab-gen` followed by `cyberlab-gen generate ...` would fail at load time with a "Bundled registry file not found" error.

Phase 0 runs everything from the source tree via `uv run`. The Phase-0 mechanical-consistency smoke tests (`implementation-plan.md §3.4` check 4) all execute against the source-tree path. No CI or local-dev path exercises the wheel-install case. Therefore the wheel-packaging gap doesn't block Task 4's exit criteria.

## Alternatives considered

- **Move `registry/` into `cyberlab_gen/registry/` (a subpackage).** Rejected for Task 4 — touches the repo layout that `CLAUDE.md`'s project map describes and that Task 0 / `implementation-plan.md §3.2` established. Requires updating every internal reference plus the docs. Not the right scope for the loader task.
- **Add `[tool.hatch.build.targets.wheel.force-include]` to ship `registry/` as wheel data.** Rejected for Task 4 — Phase 0 has no distribution story (no PyPI publish, no install-test, no end-user invocation outside `uv run`). Adding the configuration now without a way to verify it is configuration without coverage; CLAUDE.md's "Don't add features... beyond what the task requires" applies.
- **Resolve via `Path(cyberlab_gen.__file__).parent.parent / "registry"` and defer the wheel question until distribution lands.** Chosen.

## Consequences

- `cyberlab_gen/registries/loader.py::bundled_registry_dir` does the parent-of-parent resolution. Works for `uv run`, `pip install -e .` (editable installs preserve the source tree), and pytest under the source tree.
- A future wheel-distribution task must do one of:
  - Move `registry/` into the package and switch the resolver to `importlib.resources.files("cyberlab_gen.registry")`. (Cleaner long-term; package layout convention.)
  - Add `force-include` to `[tool.hatch.build.targets.wheel]` and keep the current resolver but add a fallback that walks both candidate paths.
- The Phase-0 smoke test (`test_load_bundled_yields_complete_layer`) does NOT exercise the wheel path. A wheel-install regression test must be added when the distribution story lands.
- The overlay-directory resolver (`default_overlay_dir`) is also a Phase-0 stopgap (`Path.home() / ".cyberlab-gen" / "registry-overlay"`); Task 6's `LocalState` is the real owner. The loader's `overlay_dir: Path | None = None` parameter lets `LocalState` swap in cleanly without modifying any caller.

## Supersedes

None.
