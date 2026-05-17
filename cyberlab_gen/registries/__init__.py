"""Registries subpackage — bundled + overlay registry loading and merging.

Implements the bundled (read-only) + user-overlay (writable) hierarchy per
`docs/schema.md §4.11`, with overlay-wins semantics. Loader, merge, and the
`MergedRegistries` accessor live here. Architectural source:
`docs/schema.md §4.11` and `docs/registry-details.md`.
"""
