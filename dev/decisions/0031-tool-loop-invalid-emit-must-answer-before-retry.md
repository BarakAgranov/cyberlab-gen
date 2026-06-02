# 0031 — Tool loop: answer an invalid emit's tool_use before the forced-extract retry

**Date:** 2026-06-02
**Phase:** 1 (provider-backed eval unblocking; the tool-loop 400, diagnosed from real evidence)
**Architecture refs:** `provider-interface.md §4.1` (content-shape contract), §6.2 (malformed-output retry), ADR 0027 (adapter design), ADR 0029 (answer every tool_use in a turn)

## Decision

In `AnthropicProvider.complete_with_tools`, the **finish branch** (`emit_use is not None and not real_uses`) handles the case where the model called the forced `emit` tool but its arguments fail `output_schema` validation. It now **appends a `tool_result` answering the emit `tool_use`** (carrying the validation error) before delegating to `_extract_structured`. Previously it appended only the assistant turn (which contains the emit `tool_use`) and handed that to `_extract_structured`, whose first API request therefore carried a **trailing, unanswered `tool_use`** → Anthropic 400 (`tool_use ids were found without tool_result blocks immediately after`).

The loop invariant, now upheld on every path: **no API request is ever sent whose final assistant turn has an unanswered `tool_use` block.**

The `max_iterations`-exhaustion path was verified to already comply (it `raise`s `ToolLoopError` after the loop without making a further call), so it is unchanged; a test now locks that invariant.

The temporary stderr message-array dump from the instrumentation pass is **gated behind `CYBERLAB_GEN_DEBUG_TOOL_LOOP`** (off by default) so normal runs are quiet; `_debug_summarize_messages` and its unit test are kept as a one-flag diagnostic.

## Context

Two prior fixes (ADR 0029: answer every `tool_use` in a *real-tool* turn; and the multi-tool work) did not stop the 400 because they were tested against fakes that never reproduced the actual failing shape. Instrumentation (the message-array dump) captured it: every failure's final assistant turn was a single `emit` `tool_use` with no following `tool_result`, while *all earlier turns were paired correctly*. So result-assembly was fine; the defect was loop control on the **coercion path**, exactly here:

```python
except PydanticValidationError:
    convo.append({"role": "assistant", "content": content})   # content = [emit tool_use]
    output = await self._extract_structured(base_messages=convo, ...)  # sends the dangling emit
```

The Extractor forces a large `AttackSpec`; the model frequently emits an `AttackSpec` that fails Pydantic validation, which is precisely what drives this branch — so it fired on essentially every real run.

## Alternatives considered

- **Don't append the failed emit turn at all** (seed `_extract_structured` with the prior balanced `convo`) — valid and 400-safe, but discards the model's failed attempt and the specific validation error, so the retry is blind. Rejected in favour of answering the emit with the error (better-grounded retry, mirrors `_extract_structured`'s own internal invalid-args handling).
- **Sanitize the array right before every `_create`** (strip/أnswer any trailing dangling `tool_use`) — a defensive catch-all, but it hides the control-flow bug rather than fixing it and could mask future regressions. Rejected; the focused fix plus the contract-checking test fake is clearer.
- **Leave the instrumentation firing on every 4xx** — rejected as noisy; gated behind an env var instead.

## Consequences

- `complete_with_tools` upholds the no-dangling-`tool_use` invariant on all paths: real-tool turns (ADR 0029), the invalid-emit coercion (this ADR), text-only coercion (no `tool_use`), and `max_iterations` exhaustion (raises, no call).
- New tests (`tests/unit/providers/test_anthropic_provider.py`) using the contract-checking fake client that raises the real 400 on an unbalanced array:
  - invalid-emit-args finish → the forced retry's request answers the emit's `tool_use` first; **fails on the old loop** (HardFailure/400 with the dumped `[1] assistant tool_use=['e1'] <<< MALFORMED` shape), passes on the fix.
  - `max_iterations` reached with tools pending → `ToolLoopError`, no malformed call. (Passes on both — that path was already correct; the test locks the invariant.)
- Instrumentation is retained but off by default (`CYBERLAB_GEN_DEBUG_TOOL_LOOP`); the `_debug_summarize_messages` MALFORMED-flag unit test stays.
