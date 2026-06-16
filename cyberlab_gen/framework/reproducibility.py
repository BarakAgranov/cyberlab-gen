"""Lab-level reproducibility derivation — framework code, never the Planner.

Architectural source: ``schema.md §4.8`` (the any-heterogeneity-mixed rule),
``architecture.md §1.5`` (the framework computes deterministic rollups; LLMs do
not), ``§0.7`` (emergent lab class — the lab class is the sum of per-step
decisions). Domain pinned by **ADR 0081**: the rollup sources the AttackSpec's
canonical ``chain.chain_steps`` (not manifest ``StepBlock``s, not
``alternative_paths``), so a chain step that became a ``lab_resource`` or prereq
still contributes its tier and the rollup is complete by construction.

The lab-level block is framework-DERIVED (``AttackSpec.reproducibility`` is
``FrameworkOwned``, ADR 0087): the framework fills ``classification_lab_level``,
``caveats`` and ``derivation_trace`` and leaves the prose ``overall_assessment``
``None`` — ``§4.9`` has no honest framework ``ProvenanceSource`` for it, so a
later prose-producer authors it with a real source (ADR 0088).

Pure functions, no I/O. The post-Planner wiring that calls
``derive_lab_reproducibility`` lives in the ``plan`` graph (Phase 2 Task 6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.schemas import (
    AttackSpec,
    ChainStep,
    ReproducibilityBlock,
    ReproducibilityLabLevel,
    ReproducibilityTier,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Stable presentation order for caveats / counting (worst-to-... is not implied;
# this is just a deterministic ordering so output is reproducible run to run).
_TIER_ORDER: tuple[ReproducibilityTier, ...] = (
    ReproducibilityTier.FULL,
    ReproducibilityTier.PARTIAL_SIMULATION,
    ReproducibilityTier.DEMONSTRATION_ONLY,
    ReproducibilityTier.NOT_REPRODUCIBLE,
)


def classify_lab_level(tiers: Sequence[ReproducibilityTier]) -> ReproducibilityLabLevel:
    """Roll a list of per-step tiers up to a lab-level classification (``schema.md §4.8``).

    ``required`` = steps not dropped to ``not_reproducible``. All required share one
    tier -> that tier; required span >=2 tiers (any proportions) -> ``mixed``; no
    required steps remain (all dropped, or — defensively — an empty list) ->
    ``not_reproducible`` (the Planner turns that into a ``cannot_plan`` refusal; Task 3,
    not here). Pure, no I/O.
    """
    required = [t for t in tiers if t is not ReproducibilityTier.NOT_REPRODUCIBLE]
    if not required:
        # No reproducible step survives: the lab as a whole is not reproducible.
        return ReproducibilityLabLevel.NOT_REPRODUCIBLE
    distinct = set(required)
    if len(distinct) == 1:
        return _tier_to_lab_level(next(iter(distinct)))
    return ReproducibilityLabLevel.MIXED


def derive_lab_reproducibility(spec: AttackSpec) -> ReproducibilityBlock:
    """Derive the lab-level ``ReproducibilityBlock`` from the AttackSpec's chain (ADR 0081).

    Sources ``spec.chain.chain_steps`` (the canonical chain only). Fills the three
    framework-derived fields and leaves ``overall_assessment`` ``None`` (ADR 0088).
    Framework code, never the Planner (``architecture.md §1.5``). Pure, no I/O.

    A ``None`` chain (an out-of-scope spec is never planned, so this is defensive)
    has no reproducible step and classifies ``not_reproducible``.
    """
    steps: list[ChainStep] = spec.chain.chain_steps if spec.chain is not None else []
    tiers = [step.reproducibility.classification for step in steps]
    classification = classify_lab_level(tiers)
    return ReproducibilityBlock(
        classification_lab_level=classification,
        caveats=_build_caveats(tiers),
        derivation_trace=_build_derivation_trace(steps, classification),
    )


def _tier_to_lab_level(tier: ReproducibilityTier) -> ReproducibilityLabLevel:
    """Map a single per-step tier to its lab-level counterpart (exhaustive)."""
    match tier:
        case ReproducibilityTier.FULL:
            return ReproducibilityLabLevel.FULL
        case ReproducibilityTier.PARTIAL_SIMULATION:
            return ReproducibilityLabLevel.PARTIAL_SIMULATION
        case ReproducibilityTier.DEMONSTRATION_ONLY:
            return ReproducibilityLabLevel.DEMONSTRATION_ONLY
        case ReproducibilityTier.NOT_REPRODUCIBLE:
            return ReproducibilityLabLevel.NOT_REPRODUCIBLE


def _build_caveats(tiers: Sequence[ReproducibilityTier]) -> list[str]:
    """One line per distinct tier present, with proportions over all chain steps.

    Includes dropped (``not_reproducible``) steps so the user sees the full picture
    (``schema.md §4.8``: "surfaces which tiers are present and in what proportions").
    """
    total = len(tiers)
    step_word = "chain step" if total == 1 else "chain steps"
    lines: list[str] = []
    for tier in _TIER_ORDER:
        count = sum(1 for t in tiers if t is tier)
        if count:
            verb = "is" if count == 1 else "are"
            lines.append(f"{count} of {total} {step_word} {verb} {tier.value}")
    return lines


def _build_derivation_trace(
    steps: Sequence[ChainStep], classification: ReproducibilityLabLevel
) -> list[str]:
    """Per-step tier listing (dropped steps marked excluded) + the resulting classification."""
    lines: list[str] = []
    for step in steps:
        tier = step.reproducibility.classification
        if tier is ReproducibilityTier.NOT_REPRODUCIBLE:
            lines.append(f"{step.id}: {tier.value} (excluded from rollup)")
        else:
            lines.append(f"{step.id}: {tier.value}")
    lines.append(f"lab-level classification: {classification.value}")
    return lines
