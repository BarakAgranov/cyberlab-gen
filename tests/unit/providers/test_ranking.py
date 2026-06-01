"""Unit tests for capability-to-model resolution.

Pins from the Phase 0 Task 5b brief:

- The bundled ``model_rankings.yaml`` loads, contains the three known
  capability hints, and validates as ``ModelRankings``.
- ``is_provider_configured`` matches the Phase-0 rule recorded in ADR
  ``0011-configured-provider-phase-0.md``: mock always, anthropic via
  env var, everything else (including OpenAI) false.
- ``ProviderRegistry`` validates capability coverage **at construction**,
  not lazily at first :meth:`resolve` call — a misconfigured deployment
  must fail fast at startup. ``CapabilityUnreachable`` names the
  offending capability so users know what to fix.
- The resolver returns the **first configured** entry and skips
  unconfigured ones — no mid-call vendor fallback
  (provider-interface.md §3.7).
- Resolution is stable within a registry instance.
- The factory ``build_provider_registry`` honors the current env.
"""

import logging

import pytest

from cyberlab_gen.errors import CapabilityUnreachable
from cyberlab_gen.providers import (
    CapabilityHint,
    ModelRankings,
    ProviderRegistry,
    RankingEntry,
    build_provider_registry,
    is_provider_configured,
    load_model_rankings,
)


def _rankings(**by_capability: list[tuple[str, str]]) -> ModelRankings:
    """Build a ``ModelRankings`` from the compact (provider, model) form."""
    return ModelRankings(
        by_capability={
            CapabilityHint(name): [RankingEntry(provider=p, model=m) for p, m in entries]
            for name, entries in by_capability.items()
        }
    )


def test_load_model_rankings_bundled() -> None:
    rankings = load_model_rankings()
    assert set(rankings.by_capability.keys()) == {
        CapabilityHint.HIGH_QUALITY_REASONING,
        CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        CapabilityHint.LONG_CONTEXT_EXTRACTION,
    }
    hq = rankings.by_capability[CapabilityHint.HIGH_QUALITY_REASONING]
    assert hq[0].provider == "anthropic"
    assert hq[0].model == "claude-opus-4-8"


def test_is_provider_configured_mock_always_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert is_provider_configured("mock") is True


def test_is_provider_configured_anthropic_requires_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert is_provider_configured("anthropic") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert is_provider_configured("anthropic") is True


def test_is_provider_configured_anthropic_empty_env_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert is_provider_configured("anthropic") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert is_provider_configured("anthropic") is False


def test_is_provider_configured_openai_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "anything")
    assert is_provider_configured("openai") is False


def test_is_provider_configured_unknown_provider_is_false() -> None:
    assert is_provider_configured("some-novel-vendor") is False


def test_provider_registry_resolves_first_reachable() -> None:
    rankings = _rankings(
        high_quality_reasoning=[("anthropic", "claude-opus-4-7")],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    registry = ProviderRegistry(rankings, frozenset({"anthropic"}))
    resolved = registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-opus-4-7"


def test_provider_registry_skips_unconfigured_entries_in_order() -> None:
    rankings = _rankings(
        high_quality_reasoning=[
            ("openai", "<pinned-in-release>"),
            ("anthropic", "claude-opus-4-7"),
        ],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    registry = ProviderRegistry(rankings, frozenset({"anthropic"}))
    resolved = registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-opus-4-7"


def test_provider_registry_raises_at_construction_when_capability_unreachable() -> None:
    rankings = _rankings(
        high_quality_reasoning=[("openai", "<pinned-in-release>")],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    with pytest.raises(CapabilityUnreachable, match="high_quality_reasoning"):
        ProviderRegistry(rankings, frozenset({"anthropic"}))


def test_provider_registry_raises_when_no_providers_configured() -> None:
    rankings = _rankings(
        high_quality_reasoning=[("anthropic", "claude-opus-4-7")],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    with pytest.raises(CapabilityUnreachable):
        ProviderRegistry(rankings, frozenset())


def test_provider_registry_resolution_is_stable() -> None:
    rankings = _rankings(
        high_quality_reasoning=[("anthropic", "claude-opus-4-7")],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    registry = ProviderRegistry(rankings, frozenset({"anthropic"}))
    first = registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    second = registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    assert first is second


def test_provider_registry_logs_resolution_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rankings = _rankings(
        high_quality_reasoning=[("anthropic", "claude-opus-4-7")],
        fast_cheap_structured_output=[("anthropic", "claude-haiku-4-5-20251001")],
        long_context_extraction=[("anthropic", "claude-opus-4-7")],
    )
    registry = ProviderRegistry(rankings, frozenset({"anthropic"}))
    with caplog.at_level(logging.INFO, logger="cyberlab_gen.providers.ranking"):
        registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    assert any(
        "claude-opus-4-7" in record.getMessage() and record.levelno == logging.INFO
        for record in caplog.records
    )


def test_build_provider_registry_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory checks the env: with the key set, the bundled rankings
    construct without error; without it, ``CapabilityUnreachable`` is raised.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    registry = build_provider_registry()
    resolved = registry.resolve(CapabilityHint.HIGH_QUALITY_REASONING)
    assert resolved.provider == "anthropic"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(CapabilityUnreachable):
        build_provider_registry()
