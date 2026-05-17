"""Agents subpackage — per-agent contracts and Pydantic AI integrations.

One submodule per agent (Extractor, Planner, Generators, juries, Critic,
Repair Agent) per the inventory in `docs/agents.md §5.2`. Each agent owns its
prompt, tool list, and output schema. Empty in Phase 0; the first agent
(Extractor) lands in Phase 1.
"""
