"""LabManifest envelope and inner content blocks.

Architectural source: ``schema.md §4.4`` / ``§4.5``; exact Pydantic shapes in
``schema-details.md §5``. Per ADR 0004 every model here extends ``ArtifactModel``
(``extra="forbid"``) rather than the bare ``BaseModel + ConfigDict`` the doc
shows inline.

The manifest is the single source of truth every Phase-3+ agent reads
(``implementation-plan.md §5.1``); its shape is locked in Phase 2. Content
fields wrap in ``Provenance[T]`` (``schema.md §4.9``); structural fields (ids,
paths, value-type / execution-context references, enums-as-discriminators) are
bare. Lab-level ``reproducibility`` is *derived* by the framework, never authored
(``schema.md §4.8``; Phase-2 Task 2).
"""

import re
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator

from cyberlab_gen.schemas.attack_spec import (
    CveReference,
    DetectionBlock,
    ExtrasEntry,
    PerStepReproducibility,
    ReproducibilityBlock,
    SourceBlock,
    TradecraftNote,
)
from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import (
    IdentifierKind,
    InputSource,
    LabRole,
    OnDependencyFailure,
    PrereqKind,
    PrereqTiming,
    ProvisioningMechanism,
    Severity,
    SpecKind,
    StepComposition,
)
from cyberlab_gen.schemas.envelope import SpecEnvelope
from cyberlab_gen.schemas.primitives import (
    ExecutionContext,
    FacetName,
    KebabId,
    MitreTacticId,
    MitreTechniqueId,
    NonEmptyString,
    SemVer,
    SnakeName,
    ValueTypeName,
)
from cyberlab_gen.schemas.provenance import Provenance, ProvenanceString

# --- §5.1 CoreBlock --------------------------------------------------------


class GenerationBlock(ArtifactModel):
    """Run metadata stamped at generation. schema-details.md §5.1.

    ``model`` is framework-stamped from the cost ledger (ADR 0065), never the
    Planner's self-report.
    """

    tool_version: SemVer
    model: NonEmptyString
    timestamp: datetime


class CoreBlock(ArtifactModel):
    """Non-optional metadata about the lab. schema.md §4.4; schema-details.md §5.1.

    ``reproducibility`` reuses the AttackSpec's ``ReproducibilityBlock``; its
    ``classification_lab_level`` is *derived* from per-step tiers (Phase-2 Task 2),
    not authored. ``cve_references`` is the manifest-side CVE list (carried forward
    by the Planner), distinct from the AttackSpec's ``external_references.cves``.
    """

    id: KebabId
    name: NonEmptyString
    source: SourceBlock
    mitre_tactics: list[MitreTacticId] = Field(default_factory=list[MitreTacticId])
    thesis: ProvenanceString
    severity: Provenance[Severity]
    cve_references: list[CveReference] = Field(default_factory=list[CveReference])
    reproducibility: ReproducibilityBlock
    generation: GenerationBlock


# --- §5.2 PrereqsBlock / PrereqBlock ---------------------------------------


class PrereqBlock(ArtifactModel):
    """A single prerequisite. schema.md §4.4; schema-details.md §5.2."""

    id: KebabId
    description: NonEmptyString
    kind: PrereqKind
    timing: PrereqTiming
    check_command: NonEmptyString | None = None  # required for non-manual kinds
    fix_command: NonEmptyString | None = None  # required for auto_fixable
    consent_prompt: NonEmptyString | None = None
    applies_to_phase: KebabId | None = None  # required for mid_lab

    @model_validator(mode="after")
    def _kind_rules(self) -> Self:
        # Required-when invariants.
        if self.kind is not PrereqKind.MANUAL and self.check_command is None:
            raise ValueError("check_command required for non-manual prereqs")
        if self.kind is PrereqKind.AUTO_FIXABLE and self.fix_command is None:
            raise ValueError("fix_command required for auto_fixable prereqs")
        if self.timing is PrereqTiming.MID_LAB and self.applies_to_phase is None:
            raise ValueError("applies_to_phase required for mid_lab prereqs")
        # Negative invariants — protect against stale fields after refinement re-runs.
        if self.kind is PrereqKind.MANUAL and self.fix_command is not None:
            raise ValueError("fix_command must be None for manual prereqs")
        if self.timing is PrereqTiming.PRE_LAB and self.applies_to_phase is not None:
            raise ValueError("applies_to_phase must be None for pre_lab prereqs")
        return self


class PrereqsBlock(ArtifactModel):
    """Pre-lab / mid-lab prerequisite split. schema.md §4.4; schema-details.md §5.2."""

    pre_lab: list[PrereqBlock] = Field(default_factory=list[PrereqBlock])
    mid_lab: list[PrereqBlock] = Field(default_factory=list[PrereqBlock])


# --- §5.3 InputBlock -------------------------------------------------------


class InputBlock(ArtifactModel):
    """A typed value the user supplies at lab-run time. schema.md §4.4; schema-details.md §5.3."""

    name: SnakeName
    type: ValueTypeName
    source: InputSource
    # Open-shape: validated structurally against the value-type's JSON Schema at Layer 1.
    # ANN401 fires only on function signatures, not Pydantic field annotations.
    default: Any | None = None
    description: NonEmptyString | None = None

    @model_validator(mode="after")
    def _default_rule(self) -> Self:
        # Required-when invariant.
        if self.source is InputSource.CLI_FLAG_OR_DEFAULT and self.default is None:
            raise ValueError("default required when source is cli_flag_or_default")
        # Negative invariant — protect against stale defaults after refinement re-runs.
        if self.source is not InputSource.CLI_FLAG_OR_DEFAULT and self.default is not None:
            raise ValueError("default must be None when source is not cli_flag_or_default")
        return self


# --- §5.4 LabResourceBlock -------------------------------------------------


class DiscoveryBlock(ArtifactModel):
    """Optional discovery commands for a lab resource. schema.md §4.4; schema-details.md §5.4."""

    shortcut_command: NonEmptyString
    attacker_command: NonEmptyString


class LabResourceBlock(ArtifactModel):
    """Pre-existing world state the lab provisions. schema.md §4.4; schema-details.md §5.4.

    ``lab_role`` is a non-empty list; a resource can play several roles at once
    (e.g. ``[defender_infrastructure, attack_target]``). Layer 3 relaxes
    security-finding strictness on resources whose roles include ``attack_target``.
    """

    id: KebabId
    type: ValueTypeName
    intended_iac_resource_type: NonEmptyString  # e.g. "aws_s3_bucket"; from the cloud catalog
    provisioning_mechanism: ProvisioningMechanism
    lab_role: list[LabRole] = Field(min_length=1)
    role_notes: dict[LabRole, NonEmptyString] = Field(default_factory=dict[LabRole, NonEmptyString])
    description: ProvenanceString
    discovery: DiscoveryBlock | None = None
    # Free-form properties consumed by the Lab-level Generator; structurally validated only.
    properties: dict[str, Any] = Field(default_factory=dict[str, Any])


# --- §5.5 PhaseBlock -------------------------------------------------------


class PhaseBindInput(ArtifactModel):
    """A phase input bound to a prior phase's output. schema-details.md §5.5."""

    name: SnakeName
    type: ValueTypeName
    source_phase_output: NonEmptyString  # "phase-N.output_name"


class PhaseOutput(ArtifactModel):
    """A typed output a phase produces. schema-details.md §5.5."""

    name: SnakeName
    type: ValueTypeName


class ProducesWorldState(ArtifactModel):
    """State a phase creates outside the lab's IaC. schema.md §4.5; schema-details.md §5.5.

    The ``identifier_kind`` discrimination is critical for cleanup correctness:
    a ``static`` name carries ``identifier``; a ``runtime_generated`` name carries
    ``identifier_source`` pointing into the phase's declared outputs (Layer 2
    resolves it). The XOR prevents cleanup code from orphaning resources.
    """

    type: ValueTypeName
    identifier_kind: IdentifierKind
    identifier: NonEmptyString | None = None  # required for static
    identifier_source: NonEmptyString | None = None  # required for runtime_generated
    description: ProvenanceString

    @model_validator(mode="after")
    def _identifier_rules(self) -> Self:
        if self.identifier_kind is IdentifierKind.STATIC:
            if not self.identifier or self.identifier_source:
                raise ValueError(
                    "static identifier_kind requires identifier, not identifier_source"
                )
        else:  # RUNTIME_GENERATED
            if not self.identifier_source or self.identifier:
                raise ValueError(
                    "runtime_generated identifier_kind requires identifier_source, not identifier"
                )
        return self


class PhaseImplementation(ArtifactModel):
    """Where the phase's code lives. schema.md §4.5; schema-details.md §5.5; ADR 0079.

    ``path`` is **optional** because the manifest is one incrementally-built model
    (``architecture.md §1.1`` rejects a draft/final split) and Layer 1 runs after
    every stage: the Planner emits a skeleton phase with **no code and no path**
    (``agents.md §5.7``), so a required ``path`` would fail the Planner's own
    Layer-1 validation. The honest invariant is ``path`` ⟺ file: a skeleton has
    neither; the Per-phase Generator (Phase 3) materializes the file and the path
    together via the ``id`` → path derivation (``agents.md §9.4``).

    TODO(phase-3): Layer 2 (post-generation) is the enforcement seam — it checks
    ``path == derive(id)`` and that the file exists. Do not enforce here.
    """

    language: Literal["python"]  # v1; widens in v1.5+
    path: NonEmptyString | None = (
        None  # e.g. "attack/phase_1_initial_access.py"; None pre-generation
    )
    entrypoint: SnakeName = "run_phase"


class StepOutput(ArtifactModel):
    """A typed output a step produces. schema-details.md §5.6."""

    name: SnakeName
    type: ValueTypeName


class StepBlock(ArtifactModel):
    """A step within a phase. schema.md §4.7; schema-details.md §5.6.

    Manifest-side step (the Generator's unit of implementation), distinct from
    the AttackSpec's narrative ``ChainStep``. ``cli_equivalent`` is illustrative;
    Layer 2 does not verify equivalence.

    ``reproducibility`` is the per-step tier the Planner carries forward
    *unchanged* from the ``ChainStep`` this step implements (``architecture.md
    §0.7``: carrying-forward is not re-evaluation). It lives here — not only on
    the AttackSpec — because the Per-phase Generator is manifest-driven and must
    implement each step "at its declared reproducibility tier" (``agents.md
    §5.9``); its AttackSpec access is its phase's prose excerpts only, not the
    structured tier. Lab-level reproducibility is derived separately from the
    AttackSpec's chain steps (``CoreBlock.reproducibility``; Task 2). See ADR 0081.
    """

    id: KebabId
    step_number: int | str  # same int-or-"N.N" semantics as ChainStep
    title: NonEmptyString
    description: ProvenanceString
    function_name: SnakeName
    mitre_techniques: list[MitreTechniqueId] = Field(default_factory=list[MitreTechniqueId])
    detections: list[DetectionBlock] = Field(default_factory=list[DetectionBlock])
    reproducibility: PerStepReproducibility
    cli_equivalent: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    outputs: list[StepOutput] = Field(default_factory=list[StepOutput])
    tradecraft_notes: list[TradecraftNote] = Field(default_factory=list[TradecraftNote])
    extras: list[ExtrasEntry] = Field(default_factory=list[ExtrasEntry])

    @field_validator("step_number")
    @classmethod
    def _validate_step_number(cls, v: int | str) -> int | str:
        # Same int>=1 / dotted-"N.N" syntax as the AttackSpec ChainStep (the docstring's
        # "same int-or-'N.N' semantics" promise, now enforced rather than only described).
        if isinstance(v, int) and v < 1:
            raise ValueError("integer step_number must be >= 1")
        if isinstance(v, str) and not re.match(r"^\d+(\.\d+)+$", v):
            raise ValueError("string step_number must match N.N(.N)* (e.g., '1.2')")
        return v


class PhaseBlock(ArtifactModel):
    """A phase within the manifest. schema.md §4.5; schema-details.md §5.5."""

    id: KebabId
    name: NonEmptyString
    display_name: NonEmptyString
    short_description: NonEmptyString
    mitre_tactics: list[MitreTacticId] = Field(default_factory=list[MitreTacticId])
    implements_chain_steps: list[KebabId] = Field(default_factory=list[KebabId])
    step_composition: StepComposition
    execution_context: ExecutionContext
    on_dependency_failure: OnDependencyFailure = OnDependencyFailure.WARN
    bind_inputs: list[PhaseBindInput] = Field(default_factory=list[PhaseBindInput])
    outputs: list[PhaseOutput] = Field(default_factory=list[PhaseOutput])
    produces_world_state: list[ProducesWorldState] = Field(
        default_factory=list[ProducesWorldState],
    )
    provisioning_mechanism: ProvisioningMechanism
    references_lab_outputs: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    steps: list[StepBlock] = Field(min_length=1)
    implementation: PhaseImplementation
    extras: list[ExtrasEntry] = Field(default_factory=list[ExtrasEntry])


# --- §5.7 OutputBlock ------------------------------------------------------


class OutputBlock(ArtifactModel):
    """A lab-level output. schema.md §4.4; schema-details.md §5.7.

    Exactly one of ``iac_reference`` (an IaC output) or ``phase_output_reference``
    (a phase output exposed at lab level) is set.
    """

    name: SnakeName
    type: ValueTypeName
    description: NonEmptyString | None = None
    iac_reference: NonEmptyString | None = None  # e.g. "terraform.output.bucket_name"
    phase_output_reference: NonEmptyString | None = None  # "phase-N.output_name"

    @model_validator(mode="after")
    def _source_xor(self) -> Self:
        if bool(self.iac_reference) == bool(self.phase_output_reference):
            raise ValueError("exactly one of iac_reference or phase_output_reference must be set")
        return self


# --- §5 LabManifest envelope -----------------------------------------------

#: The schema version the framework writes for every LabManifest — **per-kind**, distinct from
#: ``CURRENT_ATTACK_SPEC_VERSION`` (ADR 0080); the two artifacts version independently.
CURRENT_MANIFEST_VERSION = 1


class LabManifest(SpecEnvelope):
    """The structured artifact produced by the Planner. schema.md §4.4.

    The single source of truth every Phase-3+ agent reads; its shape is locked
    in Phase 2 (``implementation-plan.md §5.1``). ``spec_version`` is inherited
    from ``SpecEnvelope`` and framework-stamped to ``CURRENT_MANIFEST_VERSION``
    (ADR 0069/0080). ``source`` lives in ``core`` (``CoreBlock.source``), not at the
    top level — see ADR 0080 on the per-artifact ``source`` placement.
    """

    CURRENT_VERSION: ClassVar[int] = CURRENT_MANIFEST_VERSION

    spec_kind: Literal[SpecKind.LAB_MANIFEST] = SpecKind.LAB_MANIFEST
    core: CoreBlock
    facets: list[FacetName] = Field(default_factory=list[FacetName])
    prereqs: PrereqsBlock = Field(default_factory=PrereqsBlock)
    inputs: list[InputBlock] = Field(default_factory=list[InputBlock])
    lab_resources: list[LabResourceBlock] = Field(default_factory=list[LabResourceBlock])
    phases: list[PhaseBlock] = Field(min_length=1)
    outputs: list[OutputBlock] = Field(default_factory=list[OutputBlock])
    extras: list[ExtrasEntry] = Field(default_factory=list[ExtrasEntry])
