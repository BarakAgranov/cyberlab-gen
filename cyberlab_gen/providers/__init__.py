"""Providers subpackage — LLM provider abstraction.

Houses the `Provider` ABC, capability hints, cost ledger, retry strategy, the
mock provider for tests, and per-vendor adapters. Architectural source:
`docs/provider-interface.md` and `docs/pipeline.md §3.5`. Phase 0 builds the
abstraction and scaffolds the Anthropic adapter (Tasks 5a + 5b); OpenAI lands
when its adapter is actually written (Phase 1+).
"""
