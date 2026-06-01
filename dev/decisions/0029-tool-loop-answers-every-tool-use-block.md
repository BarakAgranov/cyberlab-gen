# 0029 — Tool loop answers every tool_use block in a turn (incl. co-emitted emit and errored tools)

**Date:** 2026-06-01
**Phase:** 1 (post-provider follow-up; provider-backed eval unblocking)
**Architecture refs:** `provider-interface.md §4.1` (content-shape contract), `provider-interface.md §4.2` (ToolExecutor), ADR 0027 (this adapter's design), ADR 0021 (Extractor tool inventory)

## Decision

`AnthropicProvider.complete_with_tools` now answers **every** `tool_use` block in an assistant turn with a matching `tool_result`, in order, in the single immediately-following user message — never just the "real" tool calls. Specifically, in the tool-execution branch:

- Iterate **all** `tool_use` blocks in the assistant `content` (not only the non-emit ones).
- For each real tool → execute via `tool_executor.execute(call)` and emit its `tool_result`.
- For an **emit** tool that the model co-emitted alongside real tool calls → emit a non-error `tool_result` nudging the model to call emit again after reviewing the results, and continue the loop (its arguments were formed before the results existed, so it is premature, not authoritative).
- An executor that **raises** is converted (`_execute_tool`) into an `is_error` `tool_result` rather than aborting the call.

This is the contract Anthropic enforces: *an assistant turn with N `tool_use` blocks must be followed by a user message containing a `tool_result` for each of those N ids, before any other content — including for tool calls that errored.* Violating it returns a 400.

## Context

Every one of 6 real provider-backed extractions failed identically with:

> `messages.N: tool_use ids were found without tool_result blocks immediately after: toolu_...`

The pre-fix loop appended the model's full assistant `content` to the conversation (which can contain the forced-output **emit** `tool_use` block *and* real tool `tool_use` blocks in one turn — Claude parallel tool use), but built `tool_result` blocks only for the real (`real_uses`) calls. When the model co-emitted an emit call with real tool calls, the emit block was orphaned → 400. Separately, an executor exception propagated straight out of the loop, leaving a turn with zero `tool_result`s.

The pure case — a turn with several *real* tool calls and no emit — was already answered correctly, which is why the bug hid: the only real-call test issued 0–1 tool calls and never exercised a multi-`tool_use` turn that mixed emit with real calls or errored mid-turn.

## Alternatives considered

- **Strip the co-emitted emit block from the assistant turn instead of answering it** — valid (the API only requires results for blocks you *include*), but rewriting the model's own turn is more surprising than answering it, and answering keeps the full turn auditable in the trace/conversation. Rejected in favour of answering every block.
- **Let an executor exception propagate** (status quo) — rejected: it drops the whole turn's `tool_result`s and was a second path to the same 400; the contract says an errored tool still needs a result. Catching broad `Exception` here is deliberate (any executor failure must still yield a result) and logged.
- **Treat a co-emitted emit as the finish signal and ignore the real calls** — rejected: it discards tool calls the model explicitly requested and the results it would have seen, degrading extraction quality.

## Consequences

- `complete_with_tools` is robust to Claude parallel tool use, premature emit, and executor faults; no `tool_use` is ever left without a `tool_result`.
- New helper `_execute_tool(tool_executor, call) -> (content, is_error)`.
- Three new tests in `tests/unit/providers/test_anthropic_provider.py` use a **contract-checking fake client** that raises the real 400 when the adapter builds an unbalanced `messages` array: a multi-`tool_use` turn (2 real + emit), a multi-turn sequence with parallel real tools, and a turn where one tool raises. The first and third **fail on the pre-fix loop** and pass after; the second is multi-turn coverage.
- The Extractor's `external_lookup` returns honest stub/empty data today (see the phase-1 execution log) — independent of this fix, which only concerns the transport loop.
