# 0018 — Agent call surface and the structural-retry budget boundary

**Date:** 2026-06-01
**Phase:** Phase 1
**Architecture refs:** `provider-interface.md §6.2`, `pipeline.md §3.5`, `pipeline.md §3.7`, `architecture.md §1.5`/§1.7, ADR 0009

## Decision

There are two layered structural-output budgets, and both are honored. (1) The
**provider-internal malformed-output budget** (`provider-interface.md §6.2`,
`RetryConfig.malformed_output_max_attempts`, default 3) is owned by the
`Provider` implementation; on exhaustion it raises `errors.MalformedOutput`.
(2) The **agent-stage structural-retry budget** is owned by the new call surface
(`cyberlab_gen.agents.call_surface.AgentRunner`): it treats a `MalformedOutput`
from the provider as one structural failure of the stage and re-invokes up to
`structural_retry_attempts` (default 2 retries → 3 total stage attempts). This
second budget is what `pipeline.md §3.7` means by "counted against the retry
budget." After it is exhausted the call surface raises
`errors.AgentFailure` — the "agent-failure path" — which the orchestrator
(Task 6) will route to refinement-or-abandon per `pipeline.md §3.2.12`. This is
structural retry, never refinement (`architecture.md §1.7`).

## Context

`provider-interface.md §6.2` says malformed-output retry is a provider-layer
concern *not* charged to the agent's stage retry budget, while `pipeline.md §3.7`
and the Task 2 brief say a structurally malformed response "is retried, counted
against the retry budget" and that exhaustion raises the agent-failure path.
Read naively the two appear to contradict. The resolution is that they describe
two different budgets at two different layers; the call surface must implement
the second one so the agent-failure path is reachable.

## Alternatives considered

- **Single budget owned only by the provider** — rejected: the call surface
  could never raise the agent-failure path the brief requires, and §3.7 frames
  the structural retry at the stage level.
- **Single budget owned only by the call surface (provider does not retry)** —
  rejected: contradicts `provider-interface.md §6.2`, which assigns
  malformed-output retry (with parse-error feedback into the next vendor call)
  to the provider layer.

## Consequences

- `AgentFailure` and `ConfigError` are added to the top-level
  `cyberlab_gen/errors.py` per ADR 0009 (single hierarchy; no `agents/errors.py`).
  `AgentFailure` subclasses `CyberlabGenError` directly (agent-stage outcome,
  not a `ProviderError`). `ConfigError` is raised by the prompt loader when a
  bundled base prompt is missing.
- The call surface stays thin: resolve → call → on `MalformedOutput` retry up to
  the stage budget → on exhaustion raise `AgentFailure`. It never inspects
  content quality and never decides to refine (`architecture.md §1.5`).
- Worst-case provider attempts for a persistently malformed stream are
  `malformed_output_max_attempts × (1 + structural_retry_attempts)`; acceptable
  because each layer guards a different failure mode and both are small.
- `MockProvider` returns its registered response deterministically and never
  retries, so a dedicated failing double exercises the structural-retry →
  `AgentFailure` path; `MockProvider` covers the happy path.

## Doc-improvement note for the next brief writer

`provider-interface.md §6.2` and `pipeline.md §3.7` should cross-reference each
other so the two-layer budget is explicit; a reader can currently mistake them
for a contradiction. Not editing docs from an implementation task (per CLAUDE.md);
flagging for the architect.
