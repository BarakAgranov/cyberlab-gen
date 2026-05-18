# 0011 — "Configured provider" definition for Phase 0

**Date:** 2026-05-18
**Phase:** Phase 0 (Task 5b)
**Architecture refs:** `docs/provider-interface.md §3.4`, `docs/provider-interface.md §9`

## Decision

For Phase 0, the `is_provider_configured(name) -> bool` helper in `cyberlab_gen/providers/ranking.py` implements:

- `mock` → always `True`. The mock provider has no external dependency.
- `anthropic` → `True` iff `os.environ["ANTHROPIC_API_KEY"]` is set and non-empty after stripping whitespace.
- `openai` → always `False`. There is no OpenAI adapter in Phase 0; the bundled `model_rankings.yaml` carries `<pinned-in-release>` placeholders that are never resolvable in this phase.
- Any other provider name → `False`.

`ProviderRegistry.__init__(rankings, configured: frozenset[str])` validates at construction that every capability hint has at least one entry whose provider is in `configured`. If not, it raises `CapabilityUnreachable` naming the offending capability — fail-fast at startup, not lazy at first call.

The factory `build_provider_registry()` reads the env, builds the configured set via `is_provider_configured` for each distinct provider named in the rankings, and constructs the registry. Tests construct `ProviderRegistry` directly with arbitrary configured sets so they do not need to patch the env.

## Context

`provider-interface.md §3.4` says: "A capability hint must have at least one entry whose provider is configured. Otherwise startup fails with a clear error pointing to which capability lacks coverage." The doc does **not** define "configured" for the resolver, leaving the Phase-0 implementation to pick a concrete rule. The user's plan-review note for Task 5b explicitly asked for a Phase-0 decision: probably mock always, Anthropic via `ANTHROPIC_API_KEY`.

The same brief audit (recorded in the Task 5b plan as F3) flagged this as a Phase-0 ambiguity worth recording. Phase 1+ will extend the rule when the OpenAI adapter ships and when richer configuration (per-user overlays at `~/.cyberlab-gen/config.yaml`) is wired in.

## Alternatives considered

- **Always configure every provider that has an adapter class.** Rejected: the Anthropic adapter scaffold ships in Phase 0 but cannot actually call the API without an API key. Calling it without the key would fail at call time as a `HardFailure`; `provider-interface.md §3.4` is explicit that the resolver should not pick a provider whose call will fail. Pre-validating the key at startup is exactly the failure-mode reduction the section describes.
- **Read provider configuration from a YAML config file.** Rejected for Phase 0: there is no `LocalState` or config-loader stage yet (Task 6 lays groundwork; the full config surface lands later). An env-var probe is the minimum surface that satisfies §3.4 today without inventing a config-file shape that may not match the eventual design.
- **Put the env-var check inside `ProviderRegistry.__init__`.** Rejected: couples the resolver to environment lookup. Tests would need `monkeypatch.setenv` for every construction, and per-process state would leak across tests. The chosen split — pure `ProviderRegistry` plus a factory wiring `is_provider_configured` — keeps unit tests fully deterministic.

## Consequences

- `CapabilityUnreachable` is raised by `ProviderRegistry.__init__` and (defensively) by `resolve` if a capability has no configured entry. Phase 1+ should keep this contract when the rule widens.
- Without `ANTHROPIC_API_KEY` set, `build_provider_registry()` raises `CapabilityUnreachable` for the first uncovered capability. This is the intended startup-failure behavior — the user fixes the env and reruns.
- `mock` is always usable for tests. Test harnesses that construct registries can opt in to "mock-only" by passing `frozenset({"mock"})` directly, but the bundled `model_rankings.yaml` does not currently include any `mock` entries — that gets added if/when an `agents.md`-level need for a mock-served capability appears.
- This ADR is referenced from `cyberlab_gen/providers/ranking.py`'s module docstring.

## Supersedes

None.
