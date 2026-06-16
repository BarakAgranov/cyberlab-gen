"""LabManifest schema behavior (Phase 2 Task 1).

Covers the manifest envelope and its blocks per ``schema-details.md §5``:
representative round-trip, ``extra="forbid"``, the min-length structural
constraints, and the four cross-field validators (``ProducesWorldState``
identifier XOR, ``PrereqBlock`` kind rules, ``InputBlock`` default rule,
``OutputBlock`` reference XOR). Each test fails meaningfully if the behavior
breaks — not merely "the model instantiates".
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from cyberlab_gen.errors import SpecVersionError
from cyberlab_gen.schemas.attack_spec import (
    CURRENT_ATTACK_SPEC_VERSION,
    AttackSpec,
    ExtractionMetadataBlock,
    PerStepReproducibility,
    PublisherBlock,
    ReproducibilityBlock,
    SourceBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    IdentifierKind,
    InputSource,
    LabRole,
    PrereqKind,
    PrereqTiming,
    ProvenanceSource,
    ProvisioningMechanism,
    PublisherKind,
    ReproducibilityLabLevel,
    ReproducibilityTier,
    Severity,
    SpecKind,
    StepComposition,
)
from cyberlab_gen.schemas.loading import load_spec
from cyberlab_gen.schemas.manifest import (
    CURRENT_MANIFEST_VERSION,
    CoreBlock,
    GenerationBlock,
    InputBlock,
    LabManifest,
    LabResourceBlock,
    OutputBlock,
    PhaseBlock,
    PhaseImplementation,
    PrereqBlock,
    PrereqsBlock,
    ProducesWorldState,
    StepBlock,
)
from cyberlab_gen.schemas.provenance import CitationBlock, Provenance
from cyberlab_gen.state.run_persistence import stamp_spec_version


def _blog_str(value: str) -> Provenance[str]:
    return Provenance[str](
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _severity(value: Severity) -> Provenance[Severity]:
    return Provenance[Severity](
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _source() -> SourceBlock:
    return SourceBlock(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        title="Example Blog",
        publisher=PublisherBlock(name="Wiz", domain="wiz.io", kind=PublisherKind.VENDOR_LAB),
        fetched_at=datetime(2026, 1, 1),
        content_hash="a" * 64,
        fetch_method="http_get",
        word_count=100,
    )


def _core() -> CoreBlock:
    return CoreBlock(
        id="codebuild-lab",
        name="CodeBuild Lab",
        source=_source(),
        thesis=_blog_str("A supply-chain attack via CodeBuild."),
        severity=_severity(Severity.HIGH),
        reproducibility=ReproducibilityBlock(
            classification_lab_level=ReproducibilityLabLevel.FULL,
            overall_assessment=_blog_str("Fully reproducible."),
        ),
        generation=GenerationBlock(
            tool_version="1.0.0",
            model="claude-opus-4-8",
            timestamp=datetime(2026, 1, 1),
        ),
    )


def _step(num: int, tier: ReproducibilityTier = ReproducibilityTier.FULL) -> StepBlock:
    return StepBlock(
        id=f"step-{num}",
        step_number=num,
        title=f"Step {num}",
        description=_blog_str("Do the thing."),
        function_name=f"step_{num}",
        reproducibility=PerStepReproducibility(
            classification=tier,
            caveats=_blog_str("Carried forward from the chain step."),
            why=_blog_str("Authored by the Extractor; the Planner does not re-evaluate."),
        ),
    )


def _phase(
    idx: int,
    produces: list[ProducesWorldState] | None = None,
    tier: ReproducibilityTier = ReproducibilityTier.FULL,
) -> PhaseBlock:
    return PhaseBlock(
        id=f"phase-{idx}",
        name=f"Phase {idx}",
        display_name=f"{idx}. Phase",
        short_description="Phase description.",
        step_composition=StepComposition.SEQUENTIAL,
        execution_context="attacker_local",
        provisioning_mechanism=ProvisioningMechanism.CLI_SCRIPTS,
        produces_world_state=produces or [],
        steps=[_step(idx, tier)],
        implementation=PhaseImplementation(language="python", path=f"attack/phase_{idx}.py"),
    )


def _manifest() -> LabManifest:
    """A representative multi-phase manifest exercising every block.

    Two phases; a multi-role lab_resource; both ``identifier_kind`` values;
    a ``cli_flag_or_default`` input; a ``manual`` pre_lab prereq; an IaC output.
    """
    return LabManifest(
        spec_version=1,
        spec_kind=SpecKind.LAB_MANIFEST,
        core=_core(),
        facets=["target:aws"],
        prereqs=PrereqsBlock(
            pre_lab=[
                PrereqBlock(
                    id="aws-creds",
                    description="AWS credentials configured for the lab account",
                    kind=PrereqKind.MANUAL,
                    timing=PrereqTiming.PRE_LAB,
                ),
            ],
        ),
        inputs=[
            InputBlock(
                name="target_region",
                type="aws_region",
                source=InputSource.CLI_FLAG_OR_DEFAULT,
                default="us-east-1",
            ),
        ],
        lab_resources=[
            LabResourceBlock(
                id="logging-bucket",
                type="aws_s3_bucket",
                intended_iac_resource_type="aws_s3_bucket",
                provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                lab_role=[LabRole.DEFENDER_INFRASTRUCTURE, LabRole.ATTACK_TARGET],
                description=_blog_str("Logging bucket the attack deletes from to cover tracks."),
            ),
        ],
        phases=[
            _phase(
                1,
                produces=[
                    ProducesWorldState(
                        type="aws_s3_bucket",
                        identifier_kind=IdentifierKind.STATIC,
                        identifier="lab-attack-bucket",
                        description=_blog_str("Static-name bucket."),
                    ),
                    ProducesWorldState(
                        type="github_branch",
                        identifier_kind=IdentifierKind.RUNTIME_GENERATED,
                        identifier_source="phase_outputs.branch_name",
                        description=_blog_str("Runtime-named branch."),
                    ),
                ],
            ),
            _phase(2, tier=ReproducibilityTier.DEMONSTRATION_ONLY),
        ],
        outputs=[
            OutputBlock(
                name="bucket_name",
                type="aws_s3_bucket",
                iac_reference="terraform.output.bucket_name",
            ),
        ],
    )


def test_representative_manifest_round_trips() -> None:
    manifest = _manifest()
    restored = LabManifest.from_yaml(manifest.to_yaml())
    assert restored == manifest


def test_manifest_rejects_unknown_top_level_field() -> None:
    data = _manifest().model_dump(mode="json", by_alias=True)
    data["surprise"] = "nope"
    with pytest.raises(ValidationError):
        LabManifest.model_validate(data)


def test_manifest_requires_at_least_one_phase() -> None:
    data = _manifest().model_dump(mode="json", by_alias=True)
    data["phases"] = []
    with pytest.raises(ValidationError):
        LabManifest.model_validate(data)


def test_phase_requires_at_least_one_step() -> None:
    data = _manifest().model_dump(mode="json", by_alias=True)
    data["phases"][0]["steps"] = []
    with pytest.raises(ValidationError):
        LabManifest.model_validate(data)


def test_step_block_carries_per_step_reproducibility() -> None:
    """StepBlock carries the per-step tier the Planner forwards from the AttackSpec.

    This is the manifest's home for the per-step reproducibility the Per-phase
    Generator implements each step against (``agents.md §5.9``: "a step at its
    declared reproducibility tier"); it reuses the AttackSpec's
    ``PerStepReproducibility``, mirroring how ``CoreBlock`` reuses
    ``ReproducibilityBlock``. ADR 0081; ``schema-details.md §5.6``.
    """
    step = StepBlock(
        id="step-1",
        step_number=1,
        title="Step 1",
        description=_blog_str("Demonstrate the destructive payload."),
        function_name="step_1",
        reproducibility=PerStepReproducibility(
            classification=ReproducibilityTier.DEMONSTRATION_ONLY,
            caveats=_blog_str("Destructive payload; printed, not executed."),
            why=_blog_str("Cannot be safely executed in a lab."),
        ),
    )
    assert step.reproducibility.classification is ReproducibilityTier.DEMONSTRATION_ONLY
    restored = StepBlock.model_validate(step.model_dump())
    assert restored == step
    assert restored.reproducibility.classification is ReproducibilityTier.DEMONSTRATION_ONLY


def test_step_block_requires_reproducibility() -> None:
    """The per-step tier is required: every chain step carries one, so every
    carried-forward StepBlock does too (ADR 0081)."""
    data = _step(1).model_dump(mode="json", by_alias=True)
    del data["reproducibility"]
    with pytest.raises(ValidationError, match="reproducibility"):
        StepBlock.model_validate(data)


@pytest.mark.parametrize("bad", [0, -1, "abc", "1", "1.", ".1"])
def test_step_block_rejects_invalid_step_number(bad: int | str) -> None:
    """StepBlock.step_number enforces the same int>=1 / dotted-'N.N' syntax as the
    AttackSpec's ChainStep — the docstring promised it, the validator now enforces it (D6-05)."""
    data = {**_step(1).model_dump(mode="json", by_alias=True), "step_number": bad}
    with pytest.raises(ValidationError):
        StepBlock.model_validate(data)


@pytest.mark.parametrize("good", [1, 2, "1.2", "10.3.4"])
def test_step_block_accepts_valid_step_number(good: int | str) -> None:
    data = {**_step(1).model_dump(mode="json", by_alias=True), "step_number": good}
    assert StepBlock.model_validate(data).step_number == good


def test_lab_resource_requires_at_least_one_role() -> None:
    with pytest.raises(ValidationError):
        LabResourceBlock(
            id="x",
            type="aws_s3_bucket",
            intended_iac_resource_type="aws_s3_bucket",
            provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
            lab_role=[],
            description=_blog_str("no roles"),
        )


def test_produces_world_state_static_requires_identifier() -> None:
    with pytest.raises(ValidationError):
        ProducesWorldState(
            type="aws_s3_bucket",
            identifier_kind=IdentifierKind.STATIC,
            description=_blog_str("static missing identifier"),
        )
    # static must NOT carry identifier_source
    with pytest.raises(ValidationError):
        ProducesWorldState(
            type="aws_s3_bucket",
            identifier_kind=IdentifierKind.STATIC,
            identifier="b",
            identifier_source="phase_outputs.x",
            description=_blog_str("static with stray source"),
        )


def test_produces_world_state_runtime_requires_source() -> None:
    with pytest.raises(ValidationError):
        ProducesWorldState(
            type="github_branch",
            identifier_kind=IdentifierKind.RUNTIME_GENERATED,
            description=_blog_str("runtime missing source"),
        )
    # runtime must NOT carry a static identifier
    with pytest.raises(ValidationError):
        ProducesWorldState(
            type="github_branch",
            identifier_kind=IdentifierKind.RUNTIME_GENERATED,
            identifier="b",
            description=_blog_str("runtime with stray identifier"),
        )


def test_produces_world_state_valid_both_kinds() -> None:
    static = ProducesWorldState(
        type="aws_s3_bucket",
        identifier_kind=IdentifierKind.STATIC,
        identifier="lab-bucket",
        description=_blog_str("ok static"),
    )
    runtime = ProducesWorldState(
        type="github_branch",
        identifier_kind=IdentifierKind.RUNTIME_GENERATED,
        identifier_source="phase_outputs.branch_name",
        description=_blog_str("ok runtime"),
    )
    assert static.identifier == "lab-bucket"
    assert runtime.identifier_source == "phase_outputs.branch_name"


@pytest.mark.parametrize(
    "kwargs",
    [
        # non-manual kind missing check_command
        {"kind": PrereqKind.AUTOMATIC, "timing": PrereqTiming.PRE_LAB},
        # auto_fixable missing fix_command (has check_command)
        {
            "kind": PrereqKind.AUTO_FIXABLE,
            "timing": PrereqTiming.PRE_LAB,
            "check_command": "check",
        },
        # mid_lab missing applies_to_phase
        {"kind": PrereqKind.MANUAL, "timing": PrereqTiming.MID_LAB},
        # manual must not carry fix_command
        {"kind": PrereqKind.MANUAL, "timing": PrereqTiming.PRE_LAB, "fix_command": "fix"},
        # pre_lab must not carry applies_to_phase
        {
            "kind": PrereqKind.MANUAL,
            "timing": PrereqTiming.PRE_LAB,
            "applies_to_phase": "phase-1",
        },
    ],
)
def test_prereq_kind_rules_reject_bad_combinations(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PrereqBlock(id="p", description="d", **kwargs)  # type: ignore[arg-type]


def test_input_default_rule() -> None:
    # cli_flag_or_default requires a default
    with pytest.raises(ValidationError):
        InputBlock(name="r", type="aws_region", source=InputSource.CLI_FLAG_OR_DEFAULT)
    # a non-default source must not carry a default
    with pytest.raises(ValidationError):
        InputBlock(
            name="r",
            type="aws_region",
            source=InputSource.CLI_FLAG,
            default="us-east-1",
        )


def test_output_reference_xor() -> None:
    # neither reference set
    with pytest.raises(ValidationError):
        OutputBlock(name="o", type="aws_s3_bucket")
    # both references set
    with pytest.raises(ValidationError):
        OutputBlock(
            name="o",
            type="aws_s3_bucket",
            iac_reference="terraform.output.o",
            phase_output_reference="phase-1.o",
        )
    # exactly one is fine
    ok = OutputBlock(name="o", type="aws_s3_bucket", phase_output_reference="phase-1.o")
    assert ok.phase_output_reference == "phase-1.o"


def test_phase_implementation_path_optional() -> None:
    # The Planner emits a skeleton phase with no code and no path (ADR 0079); the
    # Generator (Phase 3) materializes the path later. A path-less implementation
    # must validate, and a manifest carrying one must round-trip.
    impl = PhaseImplementation(language="python")
    assert impl.path is None

    data = _manifest().model_dump(mode="json", by_alias=True)
    data["phases"][0]["implementation"]["path"] = None
    manifest = LabManifest.model_validate(data)
    assert manifest.phases[0].implementation.path is None
    assert LabManifest.from_yaml(manifest.to_yaml()) == manifest


# --- load gate (SpecEnvelope dispatch, ADR 0080) ---


def _min_attack_spec() -> AttackSpec:
    """A minimal valid (out-of-scope) AttackSpec for load-gate dispatch tests."""
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
        extraction_outcome_reason="Out of scope: a product announcement, not an attack writeup.",
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0",
            model="claude-opus-4-8",
            completeness_score=0.0,
            citations_count=0,
        ),
    )


def test_load_spec_dispatches_by_spec_kind() -> None:
    manifest_data = _manifest().model_dump(mode="json", by_alias=True)
    spec_data = _min_attack_spec().model_dump(mode="json", by_alias=True)
    assert isinstance(load_spec(manifest_data), LabManifest)
    assert isinstance(load_spec(spec_data), AttackSpec)


def test_load_spec_refuses_wrong_version_per_kind() -> None:
    bad_manifest = _manifest().model_dump(mode="json", by_alias=True)
    bad_manifest["spec_version"] = CURRENT_MANIFEST_VERSION + 1
    with pytest.raises(SpecVersionError):
        load_spec(bad_manifest)

    bad_spec = _min_attack_spec().model_dump(mode="json", by_alias=True)
    bad_spec["spec_version"] = CURRENT_ATTACK_SPEC_VERSION + 1
    with pytest.raises(SpecVersionError):
        load_spec(bad_spec)


def test_load_spec_rejects_unknown_spec_kind() -> None:
    data = _manifest().model_dump(mode="json", by_alias=True)
    data["spec_kind"] = "NotASpecKind"
    with pytest.raises(ValueError, match="spec_kind"):
        load_spec(data)


def test_stamp_spec_version_is_per_kind() -> None:
    # A stale manifest version is stamped to the manifest's current version, not the
    # AttackSpec's — the two kinds version independently (ADR 0080).
    stale_manifest = _manifest().model_copy(update={"spec_version": 99})
    assert stamp_spec_version(stale_manifest).spec_version == CURRENT_MANIFEST_VERSION
    stale_spec = _min_attack_spec().model_copy(update={"spec_version": 99})
    assert stamp_spec_version(stale_spec).spec_version == CURRENT_ATTACK_SPEC_VERSION
