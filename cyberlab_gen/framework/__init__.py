"""Framework subpackage — deterministic orchestration code.

Houses control-flow, routing, retry, refinement-loop coordination, and
shared-state mutation. Per `docs/architecture.md §1.5`, the framework owns the
LLM-vs-framework split: agents produce content; the framework decides routing,
retry budgets, stopping, and shipping. Empty in Phase 0; populated as the
pipeline stages land in Phase 1+.
"""
