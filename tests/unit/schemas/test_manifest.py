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

from cyberlab_gen.schemas.attack_spec import (
    PublisherBlock,
    ReproducibilityBlock,
    SourceBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    IdentifierKind,
    InputSource,
    LabRole,
    PrereqKind,
    PrereqTiming,
    ProvenanceSource,
    ProvisioningMechanism,
    PublisherKind,
    ReproducibilityLabLevel,
    Severity,
    SpecKind,
    StepComposition,
)
from cyberlab_gen.schemas.manifest import (
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


def _step(num: int) -> StepBlock:
    return StepBlock(
        id=f"step-{num}",
        step_number=num,
        title=f"Step {num}",
        description=_blog_str("Do the thing."),
        function_name=f"step_{num}",
    )


def _phase(idx: int, produces: list[ProducesWorldState] | None = None) -> PhaseBlock:
    return PhaseBlock(
        id=f"phase-{idx}",
        name=f"Phase {idx}",
        display_name=f"{idx}. Phase",
        short_description="Phase description.",
        step_composition=StepComposition.SEQUENTIAL,
        execution_context="attacker_local",
        provisioning_mechanism=ProvisioningMechanism.CLI_SCRIPTS,
        produces_world_state=produces or [],
        steps=[_step(idx)],
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
            _phase(2),
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
