# 0072 — A shared tool-using agent contract owns the six-step emit sequence

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch B, ①.6 part 1; [CONTRACT])
**Architecture refs:** `architecture.md §1.5` (LLMs produce content/judgments; the framework owns
control flow), `agents.md §5.4`/`§5.5` (the Extractor and Extractor-Jury). Source: investigation
`0004 §1.2` (S7).

## Context

`AgentRunner` (`agents/call_surface.py`) owns the call mechanics but stops below
agent-orchestration. Above it, `Extractor.extract`, `Extractor.refine`, and `ExtractorJury.review`
each **hand-rolled the identical six steps**: type-guard `registries` with
`isinstance(MergedRegistries)`, derive the registered `source_ids`, build the
`ExtractorToolExecutor`, build the system+user messages, `run_with_tools`, and unpack the typed
response. The invariants being copy-pasted are exactly the `§1.5` ones that must not drift between
agents — and Phase 2 would replicate this 6+ more times (Planner, four Generators, Critic, Repair).

## Decision

**Introduce `ToolUsingAgent` (`agents/tool_agent.py`) — a reusable agent contract above
`AgentRunner` that owns the six steps once.** It exposes a single protected
`_emit(capability, output_schema, user_content, max_tokens) -> (response, executor)`. The Extractor
and the Extractor-Jury are refactored to subclass it; each supplies only what differs (capability,
output schema, user turn, output cap) and reads the typed result — the Extractor also reads the
returned executor's side-channel (proposals + lookups), the Jury ignores it.

The `§1.5` invariants now live in exactly one place:

- `registries` is typed `MergedRegistries` — the `object` + runtime `isinstance` guards are
  **deleted** (the Tier-4 ride folded in here). The `registries` package imports neither `agents`
  nor `framework`, so the direct type is safe (no import cycle, confirmed by `pyright` + the suite).
- the model's typed output is **returned as data**; the contract never inspects it to route control
  flow.

## Alternatives considered

- **Composition (a `ToolAgentRuntime` collaborator the agents hold)** — rejected. Both agents
  genuinely *are* tool-using agents over the same registries and the same (Extractor) tool
  inventory; inheritance models that directly with less plumbing, and a base method is the natural
  home for the shared invariants. (Decision confirmed with the user before refactoring call sites.)
- **Leave the six-step hand-rolled** — rejected: the duplication is precisely the mechanism by
  which the `§1.5` invariants drift between agents (it is how the eval billed-model leak, ADR 0068,
  happened in the persistence sibling).

## Consequences

- The six-step sequence and its `§1.5` invariants live once; the Extractor and Jury behave
  identically (pinned by the unchanged `test_extractor` / `test_extractor_jury` suites plus a
  structural contract test that both are `ToolUsingAgent` subclasses and that `_emit` drives the
  call surface and returns `(response, executor)`).
- The `object` + `isinstance(MergedRegistries)` guards are gone from both agents.
- Phase 2's reviewers (Planner-Jury, Critic) and generators subclass the contract instead of
  re-copying the sequence.
- **No `docs/` edit** — this is an implementation factoring under `architecture.md §1.5`; the
  contracts and ownership table are unchanged.
