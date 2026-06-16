"""Lab-level reproducibility derivation (Phase 2 Task 2; ADR 0088).

Covers the pure ``schema.md §4.8`` any-heterogeneity-mixed rule on every branch
(the hard ``§5.5`` exit criterion) and the block-assembly that sources the
AttackSpec's canonical chain steps (ADR 0081), leaving ``overall_assessment``
None (ADR 0088 Option 2).
"""

from __future__ import annotations

import pytest

from cyberlab_gen.framework.reproducibility import (
    classify_lab_level,
    derive_lab_reproducibility,
)
from cyberlab_gen.schemas.attack_spec import (
    AlternativePath,
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    PerStepReproducibility,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityLabLevel,
    ReproducibilityTier,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from tests.unit.framework.pipeline_fakes import make_spec

FULL = ReproducibilityTier.FULL
PARTIAL = ReproducibilityTier.PARTIAL_SIMULATION
DEMO = ReproducibilityTier.DEMONSTRATION_ONLY
DROPPED = ReproducibilityTier.NOT_REPRODUCIBLE

LL = ReproducibilityLabLevel


# --- builders --------------------------------------------------------------


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _step(number: int, tier: ReproducibilityTier) -> ChainStep:
    return ChainStep(
        id=f"step-{number}",  # type: ignore[arg-type]
        step_number=number,
        title=f"Step {number}",
        description=_pstr("do the thing"),
        blog_excerpt="verbatim",
        techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
        reproducibility=PerStepReproducibility(
            classification=tier, caveats=_pstr("c"), why=_pstr("w")
        ),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _spec_with_tiers(
    tiers: list[ReproducibilityTier],
    *,
    alt_tiers: list[ReproducibilityTier] | None = None,
) -> AttackSpec:
    """An in-scope spec whose canonical chain carries ``tiers`` (and an optional alt path)."""
    steps = [_step(i + 1, t) for i, t in enumerate(tiers)]
    alt_paths: list[AlternativePath] = []
    if alt_tiers is not None:
        alt_paths = [
            AlternativePath(
                id="alt-path",  # type: ignore[arg-type]
                name="Alternative",
                description=_pstr("an alternative path"),
                chain_steps=[_step(100 + i, t) for i, t in enumerate(alt_tiers)],
                reproducibility_summary=ReproducibilityTier.DEMONSTRATION_ONLY,
            )
        ]
    chain = ChainBlock(chain_steps=steps, alternative_paths=alt_paths)
    return make_spec().model_copy(update={"chain": chain})


# --- the pure rule: classify_lab_level (schema.md §4.8) --------------------


@pytest.mark.parametrize(
    ("tiers", "expected"),
    [
        # All required steps share one tier -> that tier's lab-level classification.
        ([FULL], LL.FULL),
        ([FULL, FULL, FULL], LL.FULL),
        ([PARTIAL], LL.PARTIAL_SIMULATION),
        ([PARTIAL, PARTIAL], LL.PARTIAL_SIMULATION),
        ([DEMO], LL.DEMONSTRATION_ONLY),
        ([DEMO, DEMO, DEMO], LL.DEMONSTRATION_ONLY),
        # Required steps span multiple tiers (any proportions) -> mixed.
        ([FULL, DEMO], LL.MIXED),
        ([FULL, FULL, FULL, FULL, DEMO], LL.MIXED),  # 4:1 is still mixed
        ([FULL, PARTIAL], LL.MIXED),
        ([PARTIAL, DEMO], LL.MIXED),
        ([FULL, PARTIAL, DEMO], LL.MIXED),
        # not_reproducible steps are DROPPED before the rollup (required = not dropped).
        ([FULL, DROPPED, FULL], LL.FULL),  # homogeneous once the drop is excluded
        ([DEMO, DROPPED], LL.DEMONSTRATION_ONLY),
        ([FULL, DROPPED, DEMO], LL.MIXED),  # required set still spans tiers
        # All-dropped / n/a edge -> not_reproducible (the Planner refuses; Task 3, not here).
        ([DROPPED], LL.NOT_REPRODUCIBLE),
        ([DROPPED, DROPPED, DROPPED], LL.NOT_REPRODUCIBLE),
        # Empty list is defensive only (ChainBlock enforces >=1) -> not_reproducible.
        ([], LL.NOT_REPRODUCIBLE),
    ],
)
def test_classify_lab_level_rule(
    tiers: list[ReproducibilityTier], expected: ReproducibilityLabLevel
) -> None:
    assert classify_lab_level(tiers) is expected


def test_classify_is_proportion_independent() -> None:
    # Any heterogeneity -> mixed, regardless of how lopsided the proportions are.
    lopsided = [FULL] * 99 + [DEMO]
    assert classify_lab_level(lopsided) is LL.MIXED


# --- the block assembly: derive_lab_reproducibility (ADR 0081 / 0088) ------


def test_derive_homogeneous_full() -> None:
    block = derive_lab_reproducibility(_spec_with_tiers([FULL, FULL, FULL]))
    assert block.classification_lab_level is LL.FULL
    assert block.overall_assessment is None  # ADR 0088: framework leaves prose None
    # caveats surface tiers + proportions; the trace ends with the derived classification.
    assert any(c.startswith("3 of 3") and "full" in c for c in block.caveats)
    assert block.derivation_trace[-1] == "lab-level classification: full"
    assert len(block.derivation_trace) == 3 + 1  # one line per step + the summary


def test_derive_mixed_records_proportions() -> None:
    block = derive_lab_reproducibility(_spec_with_tiers([FULL, FULL, DEMO]))
    assert block.classification_lab_level is LL.MIXED
    assert any(c.startswith("2 of 3") and "full" in c for c in block.caveats)
    assert any(c.startswith("1 of 3") and "demonstration_only" in c for c in block.caveats)
    assert block.derivation_trace[-1] == "lab-level classification: mixed"


def test_derive_excludes_dropped_steps_but_keeps_them_visible() -> None:
    # A not_reproducible step is excluded from the rollup classification (homogeneous FULL),
    # but stays visible in the trace + caveats for honesty (schema.md §4.8 transparency).
    block = derive_lab_reproducibility(_spec_with_tiers([FULL, DROPPED, FULL]))
    assert block.classification_lab_level is LL.FULL
    assert any("not_reproducible" in c for c in block.caveats)
    assert any("excluded from rollup" in line for line in block.derivation_trace)


def test_derive_all_dropped_is_not_reproducible() -> None:
    block = derive_lab_reproducibility(_spec_with_tiers([DROPPED, DROPPED]))
    assert block.classification_lab_level is LL.NOT_REPRODUCIBLE
    assert block.overall_assessment is None


def test_derive_handles_missing_chain_defensively() -> None:
    # Out-of-scope specs (chain=None) are never planned; the rollup stays total and classifies
    # not_reproducible rather than crashing on the optional chain.
    spec = make_spec().model_copy(update={"chain": None})
    block = derive_lab_reproducibility(spec)
    assert block.classification_lab_level is LL.NOT_REPRODUCIBLE
    assert block.caveats == []


def test_derive_sources_canonical_chain_only_not_alt_paths() -> None:
    # ADR 0081: the rollup domain is the canonical chain; alternative_paths are captured-not-
    # generated in v1 and must not perturb the lab-level classification.
    block = derive_lab_reproducibility(_spec_with_tiers([FULL, FULL], alt_tiers=[DEMO, DEMO]))
    assert block.classification_lab_level is LL.FULL


def test_derived_block_round_trips() -> None:
    # The derived block is a Layer-1-valid artifact (with overall_assessment absent).
    block = derive_lab_reproducibility(_spec_with_tiers([FULL, DEMO]))
    from cyberlab_gen.schemas.attack_spec import ReproducibilityBlock

    assert ReproducibilityBlock.from_yaml(block.to_yaml()) == block
