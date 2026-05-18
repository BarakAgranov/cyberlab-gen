"""CLI subpackage — user-facing command-line entry points.

Exposes the four verbs (`generate`, `validate`, `fix`, `telemetry submit`) per
`docs/architecture.md §2.1`. User-facing output goes through `cli.output` per
`docs/coding-conventions.md §6.3`. The console-script entry point is the
``main()`` function in :mod:`cyberlab_gen.cli.main`, wired in ``pyproject.toml``
as ``cyberlab-gen = "cyberlab_gen.cli.main:main"``.

The entry point is NOT re-exported into this namespace: a top-level
``from cyberlab_gen.cli.main import main`` would shadow the
``cyberlab_gen.cli.main`` submodule with the function of the same name,
breaking attribute access to the module from importers (including pyright's
static analysis of integration tests that need to reset module-level state).
The canonical path ``cyberlab_gen.cli.main:main`` is what the console script
registers and what every importer should use. ADR 0013 records the choice.
"""
