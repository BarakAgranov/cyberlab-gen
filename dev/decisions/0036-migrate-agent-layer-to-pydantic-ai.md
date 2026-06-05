# 0036 — Migrate the agent layer to pydantic-ai (retire the hand-rolled adapter)

**Date:** 2026-06-05
**Phase:** 1 (operational-foundation prep; precedes the observability/cost/persistence pass)
**Architecture refs:** `architecture.md §1.5` ("LangGraph for orchestration, **Pydantic AI for typed agents**"), `pipeline.md §3.1`, `implementation-plan.md §4` (every agent specified as a "Pydantic AI agent"), `coding-conventions.md §10.2` (`pydantic-ai` — "typed agent layer … First used by the Extractor in Phase 1"), `provider-interface.md §4`/`§6` (call surface + errors). Supersedes the provider-layer note in ADR 0027. Builds on ADR 0018 (two-layer retry), ADR 0032 (no-progress bail), ADR 0033 (truncation halt + billed-on-raise), ADR 0035 (truncated-emit dump).

## Context

The docs commit the agent layer to pydantic-ai in four places (refs above). The
code never honoured it: `pydantic-ai` has been a declared dependency since Phase 1
Task 2 (`7e4ceb8`) but **was never imported in any commit** (`git log -S` across all
history). The provider (`AnthropicProvider`) talks to the raw `anthropic` SDK and
hand-rolls an entire agent runtime: a forced-`emit_<Schema>` structured-output
path, a tool-use loop (answer-every-`tool_use`, ADR 0029), provider-internal
malformed-output retry (ADR 0018), transient retry, message translation, a token
accumulator, truncation detection (ADR 0033) and a raw dump (ADR 0035).

An inventory found the adapter is ~1,146 lines, of which **only <150 are a thin
SDK wrapper**; the remaining ~900+ reimplement agent-framework behaviour that
pydantic-ai provides natively (`Agent(output_type=...)` forced-tool output, the
multi-step tool loop, `ModelRetry`/output-validator retries, `RunUsage`, GenAI
finish-reason). Several bugs the project has been hand-fixing map directly onto
things pydantic-ai handles: ADR 0029 (the tool loop manages it), ADR 0033 (a
truncated emit *is* a truncated tool call → pydantic-ai's `IncompleteToolCall`),
ADR 0035 (Phoenix traces capture the partial content). The deviation was never
recorded; the "Pydantic AI for typed agents" doc lines were never updated.

This also blocks the observability pass: the brief assumed Pydantic AI's native
OpenTelemetry, which cannot fire while no code goes through pydantic-ai.

## The regression risk and the spike that retired it

The only place a migration could regress the *current* operational goal is
**billed-on-raise accounting** (ADR 0033): real spend on a call that ultimately
raises (a truncated emit) must still be recorded. An offline spike (against
pydantic-ai 1.103.0 with `FunctionModel`, no API spend) established the following;
the finding is now permanently regression-tested in
`tests/unit/providers/test_anthropic_provider.py`
(`test_length_finish_reason_raises_emit_truncated_with_usage` and the billed-on-raise
assertions), so the throwaway spike script was removed:

- A truncated emit (`finish_reason='length'` + partial tool-call JSON) raises
  pydantic-ai's `IncompleteToolCall`, **and the token usage survives**: read
  `run.usage` off the `AgentRun` after the exception (graph state holds it).
- `agent.run(...)` + `except` does **not** expose usage on the exception
  (`exc.usage` is absent). **Therefore the adapter must drive `agent.iter(...)`**
  and read usage/messages from the run object in a `finally`.
- A truncation whose partial output happens to parse validly is returned
  **silently** (no raise). To preserve the strict ADR-0033 halt we add an explicit
  `finish_reason == 'length'` guard that raises `EmitTruncated`.

## Decision

Migrate the agent layer to pydantic-ai, confined to the **inside** of
`AnthropicProvider`. Every public contract is preserved so the change does not
ripple outward:

- **Unchanged:** the `Provider` ABC (`complete` / `complete_with_tools` over
  `Message`/`output_schema`/`ToolDefinition`/`ToolExecutor` → `ProviderResponse[T]`),
  the `Message`/`ToolCall`/`ToolResult` domain types, the full error hierarchy
  (incl. `EmitTruncated` and `ProviderError.usage`/`.model`), `MockProvider`,
  `CostRecordingProvider`, the `ranking` capability→model resolver, and the
  Decimal `cost_ledger` + `pricing.yaml`.
- **Rewritten:** `AnthropicProvider` builds a pydantic-ai `Agent` over
  `AnthropicModel(resolved_id, provider=AnthropicProvider(anthropic_client=<injected>))`,
  with `output_type=<schema>`, `model_settings={'max_tokens': …}`, and
  `retries={'output': N}` (this replaces the provider-internal malformed loop).
  `ToolDefinition`s become tools via `Tool.from_schema(async_fn, name, description,
  json_schema)`, whose `async_fn` calls our `ToolExecutor.execute(ToolCall(...))`.
  The loop is driven by `agent.iter(...)`, capped with
  `UsageLimits(request_limit=…)` derived from `max_iterations` (its overflow →
  `ToolLoopError`). Usage is read from `run.usage` on success and failure and
  costed via `cost_ledger.compute_cost` (Decimal, our `pricing.yaml`).

- **The two-layer retry stays.** pydantic-ai's `retries={'output': N}` is the
  *provider-internal* malformed budget (old `_extract_structured` loop).
  `AgentRunner._with_structural_retry` remains the *stage-level* structural budget
  with the ADR-0032 no-progress bail and the ADR-0033 `EmitTruncated` passthrough.
  This is the documented ADR-0018 two-layer design, not hand-rolled cruft.

- **Exception mapping** (pydantic-ai → our hierarchy), all carrying billed
  `usage`/`model`:
  - `IncompleteToolCall`, or a successful run whose final `ModelResponse.finish_reason
    == 'length'` → `EmitTruncated`.
  - `UsageLimitExceeded` from the `request_limit` cap → `ToolLoopError`.
  - other `UnexpectedModelBehavior` (output-retry budget exhausted) → `MalformedOutput`.
  - `ModelHTTPError` → `TransientFailure` for `429`/`5xx`, `HardFailure` for other
    `4xx`. Transient HTTP retry is delegated to the injected `AsyncAnthropic`
    client's own `max_retries`.

- **Observability follows.** With agents on pydantic-ai, the upcoming pass uses
  pydantic-ai **native** OTel (`Agent(instrument=True)`, GenAI semconv) exported to
  local Phoenix via `openinference-instrumentation-pydantic-ai` — **not** the
  raw-SDK `openinference-instrumentation-anthropic` (running both double-counts
  spans). This makes the original brief's premise correct.

- **`pydantic-ai` becomes a real, imported dependency**; the vestigial-dep state
  is gone.

## What is deleted

The forced-`emit` builder, `_extract_structured` malformed loop, the
`complete_with_tools` hand-rolled tool loop, `_create` transient retry + backoff,
`_translate_messages`, `_UsageAccumulator`, and the env-gated emit/tool-loop
diagnostics (`_dump_emit_on_validation_error`, `_write_truncation_dump`,
`_debug_summarize_messages`) — the ADR-0035 dump's purpose (read what the model
emitted on truncation) is now served by Phoenix traces. Net ≈ 700–900 fewer lines.

## What does NOT change (honest limits)

- **Truncation is still not *cured*.** The real fix — streaming + chunked/
  continuation emit for long blogs — remains the deferred P4 gap (ADR 0032/0033/0035).
  Migration only turns hand-rolled detection into a clean `IncompleteToolCall`/
  `finish_reason` guard; an over-long spec still fails (fast and cheap).
- **Cost stays Decimal + our `pricing.yaml`.** pydantic-ai can compute USD via
  `genai-prices` (float) but we keep `compute_cost` so by-agent/by-model rollups
  stay exact; we only consume pydantic-ai's *token* counts.

## Alternatives considered

- **Way A — keep the custom runner, drop the dep, fix the docs.** Rejected: it
  enshrines ~900 lines of bug-prone machinery and the per-future-agent tax, and
  gives only flat LLM spans (no agent/tool context) in Phoenix. Lower risk, but the
  user chose to settle the architecture in favour of the documented design.
- **Migrate the whole stack incl. the structural-retry into pydantic-ai.**
  Rejected: the two-layer budget (ADR 0018) and the ADR-0032/0033 bail/passthrough
  are architectural contracts living correctly at the call surface; collapsing them
  into pydantic-ai's single `retries` would lose the no-progress bail and the
  truncation passthrough and needlessly churn `test_call_surface.py`.
- **`agent.run` + `except`.** Rejected by the spike: usage is not on the exception,
  which would reopen the ADR-0033 billed-on-raise hole. `agent.iter` is required.

## Consequences

- `cyberlab_gen/providers/anthropic_provider.py` rewritten (~700–900 lines removed).
  Constructor keeps the injectable-`client` seam (now an `AsyncAnthropic` handed to
  pydantic-ai's `AnthropicProvider`). Capability→anthropic-model resolution stays.
- `tests/unit/providers/test_anthropic_provider.py` rewritten to assert the
  observable contract via pydantic-ai `FunctionModel`/`TestModel` offline (no API
  spend), plus the preserved custom contracts: `EmitTruncated` on `finish_reason ==
  'length'` and billed `usage`/`model` on the raised error. The live cassette
  (`tests/integration/.../test_complete_against_real_anthropic_api.yaml`) is
  re-recorded because the outgoing request shape changes.
- Docs realigned to remove the half-state: `architecture.md §1.5`,
  `pipeline.md §3.1`, `implementation-plan.md §4`, `coding-conventions.md §10.2`,
  `provider-interface.md §8` updated to describe agents on pydantic-ai over the
  retained custom `Provider`/`ranking` layer. (Per CLAUDE.md these doc edits are an
  explicit part of this architectural decision, not an incidental implementation
  edit.)
- The `agent.iter` requirement (usage survives a raise) is locked in by the new
  provider tests rather than a standalone spike script.
- Unblocks the observability pass on pydantic-ai native OTel → local Phoenix.
