# 0071 — Capability→model is resolved once; the adapter is handed the resolved id

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch A, item ①.5; [CONTRACT] — the locked `Provider` ABC)
**Architecture refs:** `provider-interface.md §3.4` / §4.1 (capability resolution + the call surface),
`architecture.md §1.5` (the framework, not a second algorithm, owns resolution). Source:
investigation `0004 §1.1` (S19), `§1.4` (S18/S46).

## Context

Capability→model was resolved **twice, by two divergent algorithms**:

- `ProviderRegistry.resolve()` returns the first entry whose provider is **configured**
  (configured-aware; the resolution used for pricing and the prompt overlay).
- `AnthropicProvider._resolve_model()` independently re-walked the adapter's **own** copy of
  `model_rankings.yaml` (loaded in `__init__`) and returned the first **anthropic** entry —
  **ignoring configured-ness** — for the actual call.

They agreed only because anthropic is the sole configured provider today and is first in every
ranking list. The moment a second provider is configured and ranked first (a day-one Phase-2
concern — `model_rankings.yaml` already lists OpenAI entries), the registry would resolve, say,
`openai/model-top` for pricing while the adapter still billed the first anthropic entry. The
**billed/reported model would diverge from the resolved/priced one** — a latent mis-billing trap.

## Decision

**Resolve `(provider, model)` exactly once — in the call surface — and pass the concrete model id
down; the adapter never re-reads rankings.** `Provider.complete` / `complete_with_tools` gain a
`model: str` parameter (the [CONTRACT] change to the locked ABC). `AgentRunner.run` /
`run_with_tools` call `resolve_model(capability)` (the single resolver, `ProviderRegistry`) and pass
the resolved `model`. `AnthropicProvider` uses the passed id and its divergent `_resolve_model` +
private `self._rankings` copy are **deleted**. `capability` stays on the call for cost attribution
and mock-response routing — never for resolution.

All implementors are updated (`AnthropicProvider`, `MockProvider`, `CostRecordingProvider`); the
mock records `last_model` as a test hook.

## Alternatives considered

- **Keep capability-only and add an optional `model` override** — rejected. It leaves two resolution
  paths and a "which wins" ambiguity; the goal is exactly one resolver.
- **Replace `capability` with `model` entirely on the ABC** — rejected. The mock routes responses by
  `capability` and the cost ledger attributes by it; keeping `capability` is lower-risk and it is
  still the meaningful *intent* the agent expresses.
- **Patch only the adapter to honour configured-ness** — rejected. That keeps two algorithms over
  the same rankings file; the divergence could silently reappear. Deleting the second algorithm is
  the durable fix.

## Consequences

- The model the adapter prices/reports **equals** the model the registry resolved. Pinned: the call
  surface passes the registry-resolved id (`test_run_passes_registry_resolved_model_to_provider`,
  using a both-providers-configured ranking where the resolved `model-top` ≠ the first anthropic
  `model-second`), and the adapter honours a passed model over the capability default
  (`test_complete_uses_passed_model_over_the_capability_default`, which fails on the old
  re-resolving adapter).
- Multi-provider **dispatch** (routing a resolved non-anthropic model to the right adapter) is still
  Tier-3 (③.3, deferred to Phase 2): ①.5 is the *correctness* prerequisite, not the dispatch build.
  The CLI still constructs one `CostRecordingProvider(AnthropicProvider())`; with the registry now
  authoritative, a resolved non-anthropic model surfaces loudly rather than being silently
  mis-billed as anthropic.
- **No `docs/` edit** — `provider-interface.md §3.4` already says resolution is the registry's job
  ("the adapter was invoked once `ProviderRegistry` resolved the capability"); the code now matches
  by removing the adapter's redundant second resolution.
