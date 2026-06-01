"""The cyberlab-gen eval harness.

Top-level, a sibling of ``cyberlab_gen/`` and **not part of the installed
package** (``CLAUDE.md`` project map; ``eval.md §7.13`` co-location). The harness
measures the pipeline it sits next to: blog-set manifest, per-blog metric runner,
manual jury-decision review tooling, and the archived eval reports.

Phase 1 additions live under :mod:`eval.runner` (``implementation-plan.md §4.2``
"Eval harness Phase 1 additions"). Invoked via ``just eval`` (``coding-conventions.md
§10``), not pytest; ``tests/eval/`` carries the smoke test that the harness starts.
"""
