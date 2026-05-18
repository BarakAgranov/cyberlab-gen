"""Capability-to-model resolution.

Architectural source: ``provider-interface.md`` §3 (capability hints and
resolution), §9 (configuration), §6.4 (``CapabilityUnreachable``).
Phase 0 Task 5b.

Resolution rules per §3.4:

- A capability hint must have **at least one entry whose provider is
  configured**. Otherwise startup fails with a clear error pointing to
  which capability lacks coverage.
- The first reachable entry in the ranked list wins. Reachability is at
  resolution time only: a provider with a configured API key is reachable
  for resolution. If the call that follows fails because the key is
  invalid or quota is exceeded, that is a ``HardFailure`` (§6.3) — the
  resolver does NOT silently fall back to the next entry. Mid-call vendor
  fallback is exactly the behavior ``pipeline.md`` §3.7 forbids.
- The same hint inside a single run always resolves to the same model.

Phase-0 definition of "configured" (recorded in
``dev/decisions/0011-configured-provider-phase-0.md``):

- ``mock`` is always configured (no external dependency).
- ``anthropic`` is configured iff ``ANTHROPIC_API_KEY`` env var is set
  and non-empty after stripping whitespace.
- ``openai`` is not configured in Phase 0 — no env-var check defined;
  the ``<pinned-in-release>`` placeholder model strings are never
  resolvable.
- Any other provider name is unconfigured by default.

The factory :func:`build_provider_registry` performs the env check and
wires the configured set into :class:`ProviderRegistry`. The class itself
takes the configured set as a constructor argument and stays pure, so
unit tests can construct registries with arbitrary configured sets
without env-patching.
"""

import logging
import os
from pathlib import Path

from pydantic import ConfigDict
from ruamel.yaml import YAML

from cyberlab_gen.errors import CapabilityUnreachable
from cyberlab_gen.providers.base import CapabilityHint
from cyberlab_gen.schemas.base import ArtifactModel

logger = logging.getLogger(__name__)


class RankingEntry(ArtifactModel):
    """One (provider, model) ranking entry. ``provider-interface.md`` §3.3."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str


class ModelRankings(ArtifactModel):
    """Loaded ``model_rankings.yaml`` indexed by capability hint.

    The on-disk YAML is a mapping from capability hint string to a list
    of ``{provider, model}`` entries; in memory the same shape lives under
    the single ``by_capability`` field so the model can be validated by
    ``ArtifactModel.model_validate``. The loader in
    :func:`load_model_rankings` wraps the raw YAML into
    ``{"by_capability": ...}``.
    """

    model_config = ConfigDict(frozen=True)

    by_capability: dict[CapabilityHint, list[RankingEntry]]


def load_model_rankings() -> ModelRankings:
    """Load the bundled ``cyberlab_gen/providers/model_rankings.yaml`` file.

    Phase 0: bundled-only. User overlay replacement
    (``~/.cyberlab-gen/model_rankings.yaml``) is deferred to a later
    task; the architecture documents that the user file REPLACES (not
    merges with) the bundled file.
    """
    path = Path(__file__).resolve().parent / "model_rankings.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))
    return ModelRankings.model_validate({"by_capability": data or {}})


def is_provider_configured(name: str) -> bool:
    """Return whether ``name`` is configured to receive calls in this run.

    Phase 0 rule set; see module docstring and ADR 0011.
    """
    if name == "mock":
        return True
    if name == "anthropic":
        return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    return False


class ProviderRegistry:
    """Resolves ``CapabilityHint`` to ``(provider, model)``.

    Construction-time validation: every capability in ``rankings`` must
    have at least one entry whose provider is in ``configured``. The
    first such failure raises :class:`CapabilityUnreachable` naming the
    offending capability — not lazily at first :meth:`resolve` call, so
    a misconfigured deployment fails fast at startup.

    Plain class (not a Pydantic model) because the configured set is a
    runtime fact, not an artifact, and the cached resolutions below are
    in-memory state.
    """

    def __init__(self, rankings: ModelRankings, configured: frozenset[str]) -> None:
        self._rankings = rankings
        self._configured = configured
        self._cache: dict[CapabilityHint, RankingEntry] = {}
        self._validate_full_coverage()

    def _validate_full_coverage(self) -> None:
        for hint, entries in self._rankings.by_capability.items():
            if not any(e.provider in self._configured for e in entries):
                raise CapabilityUnreachable(
                    f"Capability {hint.value!r} has no configured provider; "
                    f"ranked entries: "
                    f"{[(e.provider, e.model) for e in entries]}; "
                    f"configured providers: {sorted(self._configured)}"
                )

    def resolve(self, hint: CapabilityHint) -> RankingEntry:
        """Return the first configured entry for ``hint``.

        Construction-time validation guarantees at least one such entry
        exists; this method therefore cannot raise
        :class:`CapabilityUnreachable` at call time.

        Resolution is stable within a registry instance: a hint that
        resolved to ``(provider, model)`` once will return the same
        entry on every subsequent call.
        """
        if hint in self._cache:
            return self._cache[hint]
        try:
            entries = self._rankings.by_capability[hint]
        except KeyError as exc:
            raise CapabilityUnreachable(
                f"Capability {hint.value!r} not present in model rankings"
            ) from exc
        for entry in entries:
            if entry.provider in self._configured:
                logger.info(
                    "resolved capability=%s to provider=%s model=%s",
                    hint.value,
                    entry.provider,
                    entry.model,
                )
                self._cache[hint] = entry
                return entry
        raise CapabilityUnreachable(
            f"Capability {hint.value!r} has no configured provider at resolve time"
        )


def build_provider_registry() -> ProviderRegistry:
    """Build the default registry from the bundled rankings and env vars.

    Inspects the runtime environment via :func:`is_provider_configured`
    for every distinct provider named in the rankings, then constructs a
    :class:`ProviderRegistry` with the resulting configured set. The
    constructor validates capability coverage and raises
    :class:`CapabilityUnreachable` if any capability lacks a configured
    provider — that is the intended startup-failure behavior.
    """
    rankings = load_model_rankings()
    candidate_providers = {
        entry.provider for entries in rankings.by_capability.values() for entry in entries
    }
    configured = frozenset(p for p in candidate_providers if is_provider_configured(p))
    return ProviderRegistry(rankings, configured)


__all__ = [
    "ModelRankings",
    "ProviderRegistry",
    "RankingEntry",
    "build_provider_registry",
    "is_provider_configured",
    "load_model_rankings",
]
