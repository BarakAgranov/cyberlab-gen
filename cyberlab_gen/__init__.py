"""cyberlab-gen — agentic generator of validated, runnable cyber labs.

Top-level package. Subpackages are organized by architectural concern per
`docs/coding-conventions.md §3.1`. Architectural source of truth:
`docs/architecture.md`. Cross-subpackage imports go through each subpackage's
`__init__.py` re-exports; internal modules are not imported across boundaries.
"""
