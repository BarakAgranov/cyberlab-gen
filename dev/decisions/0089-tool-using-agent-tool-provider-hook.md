# 0089 — `ToolUsingAgent` gets an overridable tool-provider hook

**Date:** 2026-06-16
**Phase:** 2 (Task 3 — the Planner; [CONTRACT])
**Architecture refs:** `architecture.md §1.5` (LLMs produce content; the framework owns the
read/write tool split by *availability*, not prose), `agents.md §5.7` (the Planner is a
**producer** with a read-tool set distinct from the Extractor's), ADR 0072 (the
`ToolUsingAgent` contract), ADR 0078 (verify-only juries).

## Context

`ToolUsingAgent._emit` (ADR 0072) owns the six-step tool-loop sequence once — but it
**hardwired** the *Extractor's* tool inventory: it constructed `ExtractorToolExecutor` and
advertised `extractor_tool_definitions(...)` directly, with a single `verify_only` knob. That
served Phase 1's two agents (the Extractor, and the Extractor-Jury which shares the Extractor's
inventory to independently verify `external_api` responses, `agents.md §5.5`).

The Planner (Task 3) is the first agent whose tool set is **neither** of the two existing modes:

- `verify_only=False` advertises `{external_lookup, propose_value_type, propose_facet,
  propose_thesis_type}` — but value-type / thesis-type proposals are the **Extractor's authority
  alone** (`schema.md §4.16`), and `propose_facet` is deferred to Task 7. The Planner must never
  advertise them.
- `verify_only=True` advertises `{external_lookup}` — the **jury** read-only set. The brief is
  explicit (no discretion): the Planner is a **producer**, not a jury.

So neither mode expresses the Planner's slice set, and ADR 0072's promise that "Phase-2 reviewers
and generators subclass the contract instead of re-copying the sequence" quietly assumed every
agent shares the *Extractor's* inventory — which the Planner is the first to break. (The
Generators and Critic, Phases 3–4, each break it again.)

## Decision

**Factor the tools+executor construction out of `_emit` into an overridable
`_build_tools_and_executor()` hook.** `_emit` calls the hook; the six-step sequence and the
`§1.5` invariants stay in one place. The base implementation reproduces today's behaviour
exactly — `ExtractorToolExecutor` + `extractor_tool_definitions(...)`, gated by the
`verify_only_tools` constructor flag — so **the Extractor and the Extractor-Jury are
byte-unchanged** (they inherit the default hook; their `_emit` call sites are untouched).

A subclass that needs a different inventory overrides the hook. The Planner
(`agents/planner/tools.py`) returns `(planner_tool_definitions(source_ids),
PlannerToolExecutor(...))` — a producer read set that this slice is exactly `{external_lookup}`,
mechanically *without* any `propose_*` tool. `PlannerToolExecutor` subclasses
`ExtractorToolExecutor` in read-only mode, so the `external_lookup` engine (NVD resolution,
unavailable-source and rate-limit handling, ADR 0042) is **shared, not duplicated**, and the
subtype keeps `_emit`'s return type (`tuple[ProviderResponse[T], ExtractorToolExecutor]`)
unchanged — the Planner reads `executor.lookups` off it.

The read/write split stays enforced by **tool availability** (`§1.5`): the Planner is never
advertised a `propose_*` tool, and its executor refuses one defense-in-depth.

## Alternatives considered

- **Reuse `verify_only=True` for the Planner** — rejected. Functionally `{external_lookup}` is
  identical, but it labels a producer as a jury in code (a future reader sees the Planner
  constructed verify-only and mis-reads its role), and it does not extend to Task 7, where the
  Planner's producer set grows a *scoped* `propose_facet` that the Extractor's flag cannot
  express. The brief forbids the jury set for the Planner with no discretion.
- **Make `_build_tools_and_executor` abstract (every subclass implements it)** — rejected for
  this task: it would edit the Extractor and the Extractor-Jury, violating the "byte-unchanged"
  goal and widening the blast radius for no gain. A default that preserves today's behaviour is
  strictly safer.
- **A generic `ToolUsingAgent[E]` over the executor type** — deferred. Cleaner typing, but it
  forces the Extractor/Jury to re-declare their executor type and changes their class headers.
  The covariant-subtype approach (`PlannerToolExecutor <: ExtractorToolExecutor`) gets the same
  type-safety at the Planner call site with zero churn elsewhere. Revisit if a future agent needs
  an executor that is *not* an `ExtractorToolExecutor` subtype.

## Consequences

- The six-step sequence and its `§1.5` invariants still live once; the Extractor and
  Extractor-Jury behave identically (pinned by their unchanged suites + a new contract test that
  the default hook yields the Extractor inventory and the Planner's yields `{external_lookup}`,
  no `propose_*`).
- Phase-2/3 producers (Planner now; Generators, Critic later) supply their own inventory through
  the hook instead of re-copying the loop or abusing `verify_only`.
- `PlannerToolExecutor` subclassing `ExtractorToolExecutor` is a pragmatic reuse of the shared
  `external_lookup` engine, **not** a claim that the Planner *is* an Extractor. When Task 9 moves
  the lookup engine to a neutral ports module (ADR 0077 / seams ③.2), `PlannerToolExecutor` should
  compose that module directly and drop the subclassing.
- **No `docs/` edit** — this is an implementation factoring under `architecture.md §1.5`; the
  ownership table and the LLM/framework split are unchanged. Extends ADR 0072.
