# cyberlab-gen — Schema Details

**Companion to:** `architecture.md` (hub), `schema.md` (architectural layer).
**Document scope:** Exact Pydantic v2 model shapes for every artifact the system produces and consumes — AttackSpec envelope, LabManifest envelope, ProvenanceMetadata, CitationBlock, all sub-blocks, all enums, and the registry meta-schemas. Field-by-field nullability, constraints, validators, discriminators, and JSON-Schema output behavior. This is the Phase 0 lock material per `implementation-plan.md §3.5`.

This document is implementation-ready. The shapes here are what get checked into `cyberlab_gen/schemas/`. The architectural rationale for every shape lives in `schema.md`; this document does not re-argue those decisions, it just specifies them concretely.

Cross-reference pattern: every model in this document carries a `# schema.md §X.Y` comment pointing to its architectural source. The implementing agent preserves these comments in the code.

---

## 1. Conventions used in this document

All models are Pydantic v2 `BaseModel` subclasses unless noted. The configuration baseline:

```python
from pydantic import BaseModel, ConfigDict, Field

class ArtifactModel(BaseModel):
    """Base for everything that gets serialized as a final artifact (AttackSpec, Manifest).

    `extra="forbid"` is critical: unknown fields in user-edited artifacts (post-interrupt
    edits) must surface as Layer 1 validation errors, not be silently dropped.
    """
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class InternalModel(BaseModel):
    """Base for internal-only structures that don't cross the artifact boundary.

    `extra="ignore"` because internal types evolve more freely.
    """
    model_config = ConfigDict(
        extra="ignore",
        validate_assignment=False,
    )
```

YAML round-trip: every artifact model has a `to_yaml()` method (calls `ruamel.yaml` with project-standard formatting) and a `from_yaml()` classmethod. Pydantic's JSON Schema export is enabled and produces the artifact's JSON Schema for Layer 1 validation (`validation.md §6.4`).

Generic syntax: PEP 695 throughout. `class Provenance[T](BaseModel): ...`, not `Generic[T]`. This requires Python 3.12+; `pyproject.toml` pins `requires-python = ">=3.13"` (with a narrow upper bound; see ADR 0003).

Enum convention: `StrEnum` for string-valued enums (so they serialize naturally to YAML).

---

## 2. Common types and enums

### 2.1 Identifiers and primitives

```python
# cyberlab_gen/schemas/primitives.py

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

# Stable identifier strings used as registry keys, chain-step ids, phase ids, etc.
# Convention: lowercase, kebab-case for instance ids; snake_case for type names.
KebabId = Annotated[str, StringConstraints(
    pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
    min_length=1,
    max_length=128,
)]

# value_types and facets use snake_case names.
SnakeName = Annotated[str, StringConstraints(
    pattern=r"^[a-z][a-z0-9_]*$",
    min_length=1,
    max_length=128,
)]

# Facet names follow `category:name` pattern.
FacetName = Annotated[str, StringConstraints(
    pattern=r"^(target|runtime|lab_class_signal):[a-z][a-z0-9_]*$",
    min_length=3,
    max_length=128,
)]

# Tradecraft names follow `prefix:kebab-name` pattern per schema.md §4.7.
TradecraftName = Annotated[str, StringConstraints(
    pattern=r"^[a-z][a-z0-9_]*:[a-z0-9]+(-[a-z0-9]+)*$",
    min_length=3,
    max_length=128,
)]

# Content-bearing strings have a minimum length when required, enforced at the
# enclosing model level (different fields have different floors).
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]

# Semver-style version strings.
SemVer = Annotated[str, StringConstraints(
    pattern=r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$",
)]

# SHA-256 hex digests (blob content hashes).
Sha256Hex = Annotated[str, StringConstraints(
    pattern=r"^[a-f0-9]{64}$",
)]

# Union of the two registry-key shapes; ADR 0015.
RegistryKey = SnakeName | FacetName
```

`RegistryKey` is the union of the two shapes a registry entry can be keyed by: most registries key on `SnakeName`, the `facets` registry keys on `FacetName`. Any container keyed on an entry's registry key — notably `OverlayRegistryFile.proposals` (§6.6) — must admit both, or facet proposals become structurally impossible (the colon in a `FacetName` fails the `SnakeName` pattern). See ADR 0015.

### 2.2 Closed enums from the architecture

Closed enums are transcribed directly from the architecture. Extending these is a maintainer PR, not a runtime concern (`schema.md §4.7`).

```python
# cyberlab_gen/schemas/enums.py

class SpecKind(StrEnum):
    """Discriminator for the two artifact types. schema.md §4.2."""
    ATTACK_SPEC = "AttackSpec"
    LAB_MANIFEST = "LabManifest"


class ProvenanceSource(StrEnum):
    """Where a content field's value came from. schema.md §4.9."""
    BLOG_EXPLICIT = "blog_explicit"
    EXTERNAL_API = "external_api"
    LLM_INFERENCE = "llm_inference"
    UNKNOWN_FROM_BLOG = "unknown_from_blog"
    USER_PROVIDED = "user_provided"


class ConfidenceSource(StrEnum):
    """How a confidence value was produced. schema.md §4.9 line 467.

    Framework-computed (e.g., multi-call agreement, log-probability heuristics
    where exposed by the provider) is the stronger signal. Model-self-reported
    is treated as a weak signal by the Critic and juries.
    """
    FRAMEWORK_COMPUTED = "framework_computed"
    MODEL_SELF_REPORTED = "model_self_reported"


class CitationKind(StrEnum):
    """The kind of citation referenced by a CitationBlock. schema.md §4.9."""
    BLOG_PASSAGE = "blog_passage"
    EXTERNAL_API_RESPONSE = "external_api_response"
    LLM_REASONING_TRACE = "llm_reasoning_trace"
    USER_INPUT = "user_input"


class Severity(StrEnum):
    """Closed enum, v1. schema.md §4.7."""
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class DetectionComponent(StrEnum):
    """Closed enum, v1. schema.md §4.7."""
    CSPM = "CSPM"
    CWP = "CWP"
    CDR = "CDR"
    CIEM = "CIEM"
    DSPM = "DSPM"
    ASPM = "ASPM"
    ITDR = "ITDR"
    KSPM = "KSPM"
    API_SECURITY = "API_Security"
    SUPPLY_CHAIN_SECURITY = "Supply_Chain_Security"


class DetectionFormat(StrEnum):
    """Closed enum, v1. schema.md §4.7."""
    SIGMA = "sigma"
    KQL = "kql"
    SPL = "spl"
    ESQL = "esql"


class ProvisioningMechanism(StrEnum):
    """Closed enum, v1. schema.md §4.5."""
    TERRAFORM = "terraform"
    CLOUDFORMATION = "cloudformation"
    ARM_TEMPLATE = "arm_template"
    GCP_DEPLOYMENT_MANAGER = "gcp_deployment_manager"
    CLI_SCRIPTS = "cli_scripts"
    MANUAL = "manual"
    MIXED = "mixed"


class StepComposition(StrEnum):
    """How steps within a phase relate. schema.md §4.6."""
    SEQUENTIAL = "sequential"
    INDEPENDENT = "independent"


class IdentifierKind(StrEnum):
    """For produces_world_state entries. schema.md §4.5."""
    STATIC = "static"
    RUNTIME_GENERATED = "runtime_generated"


class OnDependencyFailure(StrEnum):
    """Per-phase failure policy. schema.md §4.5."""
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class LabRole(StrEnum):
    """Role a lab_resource plays in the lab. schema.md §4.4."""
    ATTACK_TARGET = "attack_target"
    ATTACKER_INFRASTRUCTURE = "attacker_infrastructure"
    DEFENDER_INFRASTRUCTURE = "defender_infrastructure"
    NEUTRAL = "neutral"


class ReproducibilityTier(StrEnum):
    """Per-step reproducibility classification. schema.md §4.8."""
    FULL = "full"
    PARTIAL_SIMULATION = "partial_simulation"
    DEMONSTRATION_ONLY = "demonstration_only"
    NOT_REPRODUCIBLE = "not_reproducible"


class ReproducibilityLabLevel(StrEnum):
    """Lab-level reproducibility classification, derived. schema.md §4.8."""
    FULL = "full"
    PARTIAL_SIMULATION = "partial_simulation"
    DEMONSTRATION_ONLY = "demonstration_only"
    NOT_REPRODUCIBLE = "not_reproducible"
    MIXED = "mixed"


class ExtractionOutcome(StrEnum):
    """Top-level discriminator for AttackSpec. schema.md §4.8."""
    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"


class PrereqKind(StrEnum):
    """How a prereq can be satisfied. schema.md §4.4."""
    MANUAL = "manual"
    AUTO_FIXABLE = "auto_fixable"
    AUTOMATIC = "automatic"


class PrereqTiming(StrEnum):
    """When in the lab lifecycle a prereq applies. schema.md §4.4."""
    PRE_LAB = "pre_lab"
    MID_LAB = "mid_lab"


class InputSource(StrEnum):
    """Where a lab input value comes from at run time. schema.md §4.4."""
    USER_CONFIG = "user_config"
    CLI_FLAG = "cli_flag"
    CLI_FLAG_OR_DEFAULT = "cli_flag_or_default"


class PublisherKind(StrEnum):
    """Categorizes the blog source for docs framing. schema.md §4.8."""
    VENDOR_LAB = "vendor_lab"
    RESEARCHER_PERSONAL = "researcher_personal"
    VENDOR_ADVISORY = "vendor_advisory"
    CONFERENCE_WRITEUP = "conference_writeup"
    OTHER = "other"


class IncidentStatus(StrEnum):
    """Real-world-incident status. schema.md §4.8."""
    UNKNOWN = "unknown"
    NONE_OBSERVED = "none_observed"
    INCIDENTS_DOCUMENTED = "incidents_documented"


class DefenderTechniqueKind(StrEnum):
    """Kind of defender technique. schema.md §4.8."""
    INVESTIGATION = "investigation"
    DETECTION_ENGINEERING = "detection_engineering"
    THREAT_HUNTING = "threat_hunting"
    FORENSIC_ANALYSIS = "forensic_analysis"


class DefenseApplicability(StrEnum):
    """Classification for a defense's nature. schema.md §4.8."""
    CUSTOMER_ACTIONABLE = "customer_actionable"
    ARCHITECTURAL_MITIGATION = "architectural_mitigation"
    DETECTION_ONLY = "detection_only"
    VENDOR_ONLY = "vendor_only"
```

### 2.3 Open-set string types

Some categories are open-set (registry-extensible). These are typed as `str` with validation against the loaded registry happening at Layer 1 (not as a Pydantic enum constraint), because the set of valid values depends on which registries are loaded:

- `ExecutionContext` (e.g., `attacker_local`) — validated against the `execution_contexts` registry.
- `ValueTypeName` (e.g., `aws_credentials`) — validated against the merged `value_types` registry.
- `ExternalDataSourceId` (e.g., `nvd`) — validated against the `external_data_sources` registry.
- `ThesisType` (e.g., `vulnerability_chain`) — validated against the thesis-types registry.
- `PublisherKind` is closed and is the enum above; `thesis.types` is open and is just `SnakeName`.

Each of these has a thin type alias and a registry validator the schema layer calls after structural parse:

```python
ExecutionContext = SnakeName
ValueTypeName = SnakeName
ExternalDataSourceId = SnakeName
ThesisType = SnakeName
```

---

## 3. The ProvenanceMetadata envelope

The pattern that wraps every content field in both AttackSpec and Manifest. `schema.md §4.9`.

```python
# cyberlab_gen/schemas/provenance.py

from typing import Annotated, Any, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CitationBlock(BaseModel):
    """A single citation supporting a provenance claim. schema.md §4.9."""
    model_config = ConfigDict(extra="forbid")

    kind: CitationKind
    reference: NonEmptyString
    # For blog_passage: a passage identifier ("§3, ¶2" or character offsets).
    # For external_api_response: API name + endpoint + response path.
    # For llm_reasoning_trace: a stable trace identifier.
    # For user_input: a run-id + interrupt-step identifier.
    location: str | None = None  # optional finer-grained pointer


class Provenance[T](BaseModel):
    """Wraps a content-field value with its source and citations.

    Every content field in AttackSpec and Manifest uses this envelope.
    Structural fields (ids, paths, type references) do NOT use this envelope.

    schema.md §4.9.
    """
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    value: T
    source: ProvenanceSource
    citations: list[CitationBlock] = Field(default_factory=list)
    # Required when source == LLM_INFERENCE. Validated below.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Discriminates framework-computed (multi-call agreement, log-probability
    # heuristics) from model-self-reported confidence. Downstream consumers
    # (Critic, juries, README presentation) weight these differently per
    # schema.md §4.9 line 467: framework-computed is the stronger signal.
    # Required whenever `confidence` is set.
    confidence_source: ConfidenceSource | None = None
    # Set by the framework (not by agents) to flag this field for surfacing
    # at the post-Extractor / post-Planner interrupt's per-field review.
    # Architectural rule for when this is set still belongs in schema.md §4.9
    # once §8.4 calibration provides a confidence threshold; no validator yet.
    requires_user_confirmation: bool = False
    # Required when source == UNKNOWN_FROM_BLOG. Validated below.
    reason: str | None = None
    # ---- discrepancy record (pipeline.md §3.2.4) ----
    # Set by the framework — not by agents — when pre-Planner enrichment
    # overrides a blog_explicit finding with an external_api authoritative
    # value. Preserves both values for the audit trail and for the Critic's
    # quality assessment (which surfaces material discrepancies in docs).
    discrepancy_with_blog: bool = False
    overridden_blog_value: T | None = None
    discrepancy_classification: Literal["material", "non_material"] | None = None

    @model_validator(mode="after")
    def _source_rules(self) -> "Provenance[T]":
        # Required-when invariants
        if self.source is ProvenanceSource.LLM_INFERENCE and self.confidence is None:
            raise ValueError("confidence is required when source is llm_inference")
        if self.source is ProvenanceSource.UNKNOWN_FROM_BLOG and not self.reason:
            raise ValueError("reason is required when source is unknown_from_blog")
        if self.source is ProvenanceSource.BLOG_EXPLICIT and not self.citations:
            raise ValueError("citations are required when source is blog_explicit")
        if self.source is ProvenanceSource.EXTERNAL_API and not self.citations:
            raise ValueError("citations are required when source is external_api")

        # confidence and confidence_source travel together
        if self.confidence is not None and self.confidence_source is None:
            raise ValueError("confidence_source is required when confidence is set")
        if self.confidence is None and self.confidence_source is not None:
            raise ValueError("confidence_source must be None when confidence is None")

        # Confidence is exclusive to LLM_INFERENCE.
        # See dev/decisions/0005-external-api-confidence.md.
        if self.confidence is not None and self.source is not ProvenanceSource.LLM_INFERENCE:
            raise ValueError(
                f"confidence is only valid when source is llm_inference (got source={self.source.value})"
            )
        if self.confidence_source is not None and self.source is not ProvenanceSource.LLM_INFERENCE:
            raise ValueError(
                f"confidence_source is only valid when source is llm_inference (got source={self.source.value})"
            )

        # Negative invariants — UNKNOWN_FROM_BLOG citations check.
        # The confidence-must-be-None case is covered by the LLM_INFERENCE-exclusive rule above.
        if self.source is ProvenanceSource.UNKNOWN_FROM_BLOG and self.citations:
            raise ValueError("citations must be empty when source is unknown_from_blog")


        # Discrepancy record invariants
        if self.discrepancy_with_blog:
            if self.overridden_blog_value is None:
                raise ValueError(
                    "overridden_blog_value is required when discrepancy_with_blog is true"
                )
            if self.discrepancy_classification is None:
                raise ValueError(
                    "discrepancy_classification is required when discrepancy_with_blog is true"
                )
            if self.source is not ProvenanceSource.EXTERNAL_API:
                raise ValueError(
                    "discrepancy_with_blog is only valid when source is external_api "
                    "(the framework overrode a blog value with an authoritative API value)"
                )
        else:
            if self.overridden_blog_value is not None:
                raise ValueError(
                    "overridden_blog_value must be None when discrepancy_with_blog is false"
                )
            if self.discrepancy_classification is not None:
                raise ValueError(
                    "discrepancy_classification must be None when discrepancy_with_blog is false"
                )
        return self


# Convenience type aliases used throughout the artifacts.
ProvenanceString = Provenance[str]
ProvenanceStringList = Provenance[list[str]]
ProvenanceFloat = Provenance[float]
ProvenanceInt = Provenance[int]
ProvenanceBool = Provenance[bool]
```

### 3.1 Helper constructors

For ergonomic agent code, the schema layer provides constructors that fail loudly when invariants are violated:

```python
def blog_explicit(value: T, citations: list[CitationBlock]) -> Provenance[T]:
    """Construct a blog_explicit-sourced provenance. Citations are required."""
    return Provenance[T](
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=citations,
    )


def llm_inference(
    value: T,
    confidence: float,
    confidence_source: ConfidenceSource,
    citations: list[CitationBlock],
    reasoning_trace: str | None = None,
) -> Provenance[T]:
    ...


def unknown_from_blog(reason: str) -> Provenance[None]:
    """For fields the blog didn't address. Reason is required."""
    return Provenance[None](
        value=None,
        source=ProvenanceSource.UNKNOWN_FROM_BLOG,
        reason=reason,
    )
```

---

## 4. The AttackSpec envelope

Specified in `schema.md §4.8`. The top-level shape:

```python
# cyberlab_gen/schemas/attackspec.py

class AttackSpec(ArtifactModel):
    """The structured artifact produced by the Extractor. schema.md §4.8."""

    spec_version: int = Field(ge=1, description="Monotonic schema version")
    spec_kind: Literal[SpecKind.ATTACK_SPEC]

    source: SourceBlock
    extraction_outcome: ExtractionOutcome
    extraction_outcome_reason: NonEmptyString | None = None  # required when out_of_scope

    # Present when extraction_outcome == IN_SCOPE; absent or empty when OUT_OF_SCOPE.
    thesis: ThesisBlock | None = None
    facets: list[FacetName] = Field(default_factory=list)
    external_references: ExternalRefsBlock | None = None
    real_world_incidents: RealWorldIncidentsBlock | None = None
    chain: ChainBlock | None = None
    defender_techniques: list[DefenderTechniqueBlock] = Field(default_factory=list)
    defenses: list[DefenseBlock] = Field(default_factory=list)
    reproducibility: ReproducibilityBlock | None = None
    gaps: list[GapEntry] = Field(default_factory=list)
    # Populated by pre-Planner enrichment (Task 4), never by an agent; declared
    # now per ADR 0017. See §4.9. A top-level index into the spec; the
    # authoritative per-field record lives in each field's own Provenance.
    material_discrepancies: list[MaterialDiscrepancy] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadataBlock

    extras: list[ExtrasEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _scope_consistency(self) -> "AttackSpec":
        if self.extraction_outcome is ExtractionOutcome.OUT_OF_SCOPE:
            # Required-when invariant
            if not self.extraction_outcome_reason or len(self.extraction_outcome_reason) < 30:
                raise ValueError(
                    "extraction_outcome_reason must be substantive (>=30 chars) "
                    "when extraction_outcome is out_of_scope"
                )
            # Negative invariants — out_of_scope specs must not carry stale planning data
            # left over from a refinement re-run that flipped scope.
            if self.thesis is not None:
                raise ValueError("thesis must be None when out_of_scope")
            if self.chain is not None:
                raise ValueError("chain must be None when out_of_scope")
            if self.real_world_incidents is not None:
                raise ValueError("real_world_incidents must be None when out_of_scope")
            if self.reproducibility is not None:
                raise ValueError("reproducibility must be None when out_of_scope")
            if self.defender_techniques:
                raise ValueError("defender_techniques must be empty when out_of_scope")
            if self.defenses:
                raise ValueError("defenses must be empty when out_of_scope")
        elif self.extraction_outcome is ExtractionOutcome.IN_SCOPE:
            if self.chain is None:
                raise ValueError("chain is required when in_scope")
            if self.thesis is None:
                raise ValueError("thesis is required when in_scope")
            if self.extraction_outcome_reason is not None:
                raise ValueError(
                    "extraction_outcome_reason must be None when in_scope "
                    "(the reason field describes why a blog was rejected)"
                )
        return self
```

### 4.1 `SourceBlock`

```python
class SourceBlock(BaseModel):
    """Provenance of the source blog. schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    canonical_url: HttpUrl
    title: NonEmptyString
    publisher: PublisherBlock
    authors: list[NonEmptyString] = Field(default_factory=list)
    published_at: datetime | None = None  # None when blog has no date
    fetched_at: datetime
    content_hash: Sha256Hex
    fetch_method: str  # e.g., "httpx", "playwright"; informational
    word_count: int = Field(ge=0)


class PublisherBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyString
    domain: NonEmptyString
    kind: PublisherKind
```

### 4.2 `ThesisBlock`

```python
class ThesisBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    types: list[ThesisType] = Field(min_length=1)
    summary: ProvenanceString
    attacker_objective: ProvenanceString
    vulnerability_story: ProvenanceString  # may be empty for TTP-chain blogs
    duration_as_described: ProvenanceString
```

### 4.3 `ChainBlock` and `ChainStep`

```python
class ChainBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_steps: list[ChainStep] = Field(min_length=1)
    alternative_paths: list[AlternativePath] = Field(default_factory=list)

    @model_validator(mode="after")
    def _step_numbers_monotonic(self) -> "ChainBlock":
        """List-level invariant: chain_steps' step_numbers must be non-decreasing.

        The per-field validator on ChainStep.step_number checks syntax. This
        list-level validator catches out-of-order lists like [1, 3, 2, '2.1'].
        The Extractor's prompt enforces ordering in practice; the schema is
        the floor of correctness, independent of prompt discipline.

        Sub-numbered steps (e.g., '2.1') sort under their parent — '2.1' may
        appear immediately after '2' and is treated as numerically equal-or-greater
        than 2 (lexicographic on the tuple of numeric components).
        """
        def _key(sn: int | str) -> tuple[int, ...]:
            if isinstance(sn, int):
                return (sn,)
            return tuple(int(part) for part in sn.split("."))

        keys = [_key(step.step_number) for step in self.chain_steps]
        for i in range(1, len(keys)):
            if keys[i] < keys[i - 1]:
                raise ValueError(
                    f"chain_steps step_number must be non-decreasing; "
                    f"step {i} ({self.chain_steps[i].step_number}) "
                    f"precedes step {i - 1} ({self.chain_steps[i - 1].step_number})"
                )
        return self


class ChainStep(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    step_number: int | str  # int or sub-numbered "1.1"; validated below
    title: NonEmptyString
    description: ProvenanceString
    blog_excerpt: NonEmptyString  # the verbatim passage(s); fed downstream as chain_step_excerpts
    techniques: ChainStepTechniques
    preconditions: list[ProvenanceString] = Field(default_factory=list)
    postconditions: list[ProvenanceString] = Field(default_factory=list)
    detections: list[DetectionBlock] = Field(default_factory=list)
    reproducibility: PerStepReproducibility
    depends_on: list[KebabId] = Field(default_factory=list)
    provisioning_mechanism: ProvisioningMechanism

    @field_validator("step_number")
    @classmethod
    def _validate_step_number(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < 1:
            raise ValueError("integer step_number must be >= 1")
        if isinstance(v, str) and not re.match(r"^\d+(\.\d+)+$", v):
            raise ValueError("string step_number must match N.N(.N)* (e.g., '1.2')")
        return v


class ChainStepTechniques(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mitre: list[Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]] = Field(
        default_factory=list,
    )
    tradecraft: list[TradecraftNote] = Field(default_factory=list)


class TradecraftNote(BaseModel):
    """schema.md §4.7."""
    model_config = ConfigDict(extra="forbid")

    name: TradecraftName | None = None  # soft convention enforced by extractor prompt
    description: ProvenanceString
    evades_what: ProvenanceString | None = None


class PerStepReproducibility(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    classification: ReproducibilityTier
    caveats: ProvenanceString
    why: ProvenanceString


class AlternativePath(BaseModel):
    """schema.md §4.8. Captured in v1, generated v1.5+."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    chain_steps: list[ChainStep] = Field(min_length=1)
    shares_steps_with_canonical: list[KebabId] = Field(default_factory=list)
    reproducibility_summary: ReproducibilityTier
```

### 4.4 `DetectionBlock`

Used inside chain steps and inside manifest phase-steps. Shared shape.

```python
class DetectionBlock(BaseModel):
    """schema.md §4.7."""
    model_config = ConfigDict(extra="forbid")

    component: DetectionComponent
    severity: Severity
    description: ProvenanceString
    soc_view: ProvenanceString
    remediation: ProvenanceString
    formats: list[DetectionFormatEntry] = Field(default_factory=list)


class DetectionFormatEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: DetectionFormat
    path: NonEmptyString  # relative path inside the generated lab; e.g., "detection/phase_1/sigma.yml"
```

### 4.5 `ExternalRefsBlock` and `RealWorldIncidentsBlock`

> **CVE field — AttackSpec side.** `ExternalRefsBlock.cves` below is the **AttackSpec's** CVE list (what the Extractor found in the blog, enriched in place by the framework's pre-Planner pass). Its sibling on the *other* artifact is `CoreBlock.cve_references` on the LabManifest (§5.1) — same `CveReference` element type, different artifact. The near-identical names are intentional siblings, not duplicates. ADR 0020's choice to enrich *this* field (`external_references.cves`) in Phase 1 is correct and stands.

```python
class ExternalRefsBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    cves: list[CveReference] = Field(default_factory=list)
    related_blogs: list[RelatedBlogReference] = Field(default_factory=list)
    advisories: list[AdvisoryReference] = Field(default_factory=list)
    mitre_techniques: list[MitreTechniqueReference] = Field(default_factory=list)


class CveReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cve_id: Annotated[str, StringConstraints(pattern=r"^CVE-\d{4}-\d{4,}$")]
    description: ProvenanceString
    cvss_score: ProvenanceFloat | None = None
    severity: Provenance[Severity] | None = None
    # Authoritative source recorded by framework after pre-Planner enrichment.
    source_of_record: ExternalDataSourceId | None = None


class RelatedBlogReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    title: ProvenanceString
    relationship: ProvenanceString


class AdvisoryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    advisory_id: NonEmptyString
    source: ExternalDataSourceId
    url: HttpUrl | None = None
    description: ProvenanceString


class MitreTechniqueReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technique_id: Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]
    name: ProvenanceString
    tactic_ids: list[Annotated[str, StringConstraints(pattern=r"^TA\d{4}$")]] = Field(
        default_factory=list,
    )


class RealWorldIncidentsBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus
    evidence_source: ProvenanceString | None = None  # required when status != UNKNOWN
    incidents: list[RealWorldIncident] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_rules(self) -> "RealWorldIncidentsBlock":
        if self.status is not IncidentStatus.UNKNOWN and self.evidence_source is None:
            raise ValueError("evidence_source required when status != unknown")
        if self.status is IncidentStatus.INCIDENTS_DOCUMENTED and not self.incidents:
            raise ValueError("incidents required when status == incidents_documented")
        if self.status is not IncidentStatus.INCIDENTS_DOCUMENTED and self.incidents:
            raise ValueError("incidents must be empty unless status == incidents_documented")
        return self


class RealWorldIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    affected_organizations: list[NonEmptyString] = Field(default_factory=list)
    attribution: NonEmptyString | None = None  # threat actor; None when unattributed
    date_range: ProvenanceString
```

### 4.6 `DefenderTechniqueBlock` and `DefenseBlock`

```python
class DefenderTechniqueBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    technique_kind: DefenderTechniqueKind
    applies_to_chain_steps: list[KebabId] = Field(default_factory=list)


class DefenseBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    description: ProvenanceString
    applicability: DefenseApplicability
    addresses_chain_steps: list[KebabId] = Field(default_factory=list)
    detection_path: NonEmptyString | None = None
    detection_format: DetectionFormat | None = None

    @model_validator(mode="after")
    def _detection_paired(self) -> "DefenseBlock":
        if (self.detection_path is None) != (self.detection_format is None):
            raise ValueError("detection_path and detection_format must be both set or both unset")
        return self
```

### 4.7 `ReproducibilityBlock` (lab-level, derived)

```python
class ReproducibilityBlock(BaseModel):
    """Lab-level reproducibility. Derived from per-step values. schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    classification_lab_level: ReproducibilityLabLevel
    caveats: list[NonEmptyString] = Field(default_factory=list)
    overall_assessment: ProvenanceString
    derivation_trace: list[NonEmptyString] = Field(default_factory=list)
```

### 4.8 `GapEntry`, `ExtractionMetadataBlock`, `ExtrasEntry`

```python
class GapEntry(BaseModel):
    """Top-level enumeration per pipeline.md §3.2.2."""
    model_config = ConfigDict(extra="forbid")

    field_path: NonEmptyString  # JSONPath-like; e.g., "chain.chain_steps[2].postconditions"
    reason: NonEmptyString
    severity: Severity


class ExtractionMetadataBlock(BaseModel):
    """schema.md §4.8."""
    model_config = ConfigDict(extra="forbid")

    extractor_version: SemVer
    model: NonEmptyString  # whatever the provider layer resolved
    completeness_score: float = Field(ge=0.0, le=1.0)
    unknown_fields: list[NonEmptyString] = Field(default_factory=list)
    citations_count: int = Field(ge=0)
    notes_for_planner: NonEmptyString | None = None


class ExtrasEntry(BaseModel):
    """The escape hatch. schema.md §4.10."""
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyString
    description: ProvenanceString
    source: ProvenanceSource
    citations: list[CitationBlock] = Field(default_factory=list)
```

### 4.9 `MaterialDiscrepancy`

The element type of the top-level `material_discrepancies` list on `AttackSpec`. Shape and rationale are recorded in `dev/decisions/0017-material-discrepancies-field.md`; this section promotes that shape into the schema doc (the code is authoritative — see `cyberlab_gen/schemas/attack_spec.py`).

A `MaterialDiscrepancy` is written **only by the framework's pre-Planner enrichment pass** (`pipeline.md §3.2.4`, `schema.md §4.9` framework-only-authorship), never by an agent, when an `external_api` value materially overrides a `blog_explicit` value. It is a top-level **index** into the spec for the Phase 1 run report (and the Phase 4 third review surface); it is **not** the authoritative record. The authoritative per-field audit trail stays in the target field's own `Provenance` (`discrepancy_with_blog` / `overridden_blog_value` / `discrepancy_classification`). The list holds only *material* discrepancies — non-material ones are a silent provenance rewrite — so the entry carries no classification field.

```python
class MaterialDiscrepancy(ArtifactModel):
    """A material blog-vs-authoritative discrepancy. ADR 0017."""

    # JSONPath-like locator, same convention as GapEntry.field_path
    field_path: NonEmptyString
    summary: NonEmptyString
    blog_value: NonEmptyString           # the original blog_explicit value (stringified)
    authoritative_value: NonEmptyString  # the overriding external_api value (stringified)
    source_of_record: ExternalDataSourceId  # which external source overrode
```

`blog_value` / `authoritative_value` are stringified summaries (not `Provenance[T]`) because this is an index surface, not the typed record. `source_of_record` reuses the `ExternalDataSourceId` alias, matching `CveReference.source_of_record` (§4.5).

---

## 5. The LabManifest envelope

Specified in `schema.md §4.4`. The top-level shape:

```python
# cyberlab_gen/schemas/manifest.py

class LabManifest(ArtifactModel):
    """The structured artifact produced by the Planner and refined by Generators.

    schema.md §4.4.
    """

    spec_version: int = Field(ge=1)
    spec_kind: Literal[SpecKind.LAB_MANIFEST]

    core: CoreBlock
    facets: list[FacetName] = Field(default_factory=list)
    prereqs: PrereqsBlock = Field(default_factory=PrereqsBlock)
    inputs: list[InputBlock] = Field(default_factory=list)
    lab_resources: list[LabResourceBlock] = Field(default_factory=list)
    phases: list[PhaseBlock] = Field(min_length=1)
    outputs: list[OutputBlock] = Field(default_factory=list)
    extras: list[ExtrasEntry] = Field(default_factory=list)
```

### 5.1 `CoreBlock`

> **CVE field — LabManifest side.** `CoreBlock.cve_references` below is the **LabManifest's** CVE list. Its sibling on the *other* artifact is `ExternalRefsBlock.cves` on the AttackSpec (§4.5) — same `CveReference` element type, different artifact, not a duplicate. Pre-Planner enrichment writes the AttackSpec's `external_references.cves`, not this field (ADR 0020); the Planner carries CVE references forward into the manifest.

```python
class CoreBlock(BaseModel):
    """schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    name: NonEmptyString
    source: SourceBlock  # same shape as AttackSpec source
    mitre_tactics: list[Annotated[str, StringConstraints(pattern=r"^TA\d{4}$")]] = Field(
        default_factory=list,
    )
    thesis: ProvenanceString
    severity: Provenance[Severity]
    cve_references: list[CveReference] = Field(default_factory=list)
    reproducibility: ReproducibilityBlock
    generation: GenerationBlock


class GenerationBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_version: SemVer
    model: NonEmptyString
    timestamp: datetime
```

### 5.2 `PrereqsBlock` and `PrereqBlock`

```python
class PrereqsBlock(BaseModel):
    """schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    pre_lab: list[PrereqBlock] = Field(default_factory=list)
    mid_lab: list[PrereqBlock] = Field(default_factory=list)


class PrereqBlock(BaseModel):
    """schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    description: NonEmptyString
    kind: PrereqKind
    timing: PrereqTiming
    check_command: NonEmptyString | None = None  # required for non-manual kinds
    fix_command: NonEmptyString | None = None  # required for auto_fixable
    consent_prompt: NonEmptyString | None = None
    applies_to_phase: KebabId | None = None  # required for mid_lab

    @model_validator(mode="after")
    def _kind_rules(self) -> "PrereqBlock":
        # Required-when invariants
        if self.kind is not PrereqKind.MANUAL and self.check_command is None:
            raise ValueError("check_command required for non-manual prereqs")
        if self.kind is PrereqKind.AUTO_FIXABLE and self.fix_command is None:
            raise ValueError("fix_command required for auto_fixable prereqs")
        if self.timing is PrereqTiming.MID_LAB and self.applies_to_phase is None:
            raise ValueError("applies_to_phase required for mid_lab prereqs")
        # Negative invariants — protect against stale fields after refinement re-runs
        if self.kind is PrereqKind.MANUAL and self.fix_command is not None:
            raise ValueError("fix_command must be None for manual prereqs")
        if self.timing is PrereqTiming.PRE_LAB and self.applies_to_phase is not None:
            raise ValueError("applies_to_phase must be None for pre_lab prereqs")
        return self
```

### 5.3 `InputBlock`

```python
class InputBlock(BaseModel):
    """schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    name: SnakeName
    type: ValueTypeName
    source: InputSource
    default: Any | None = None  # validated structurally against value-type schema at Layer 1
    description: NonEmptyString | None = None

    @model_validator(mode="after")
    def _default_rule(self) -> "InputBlock":
        # Required-when invariant
        if self.source is InputSource.CLI_FLAG_OR_DEFAULT and self.default is None:
            raise ValueError("default required when source is cli_flag_or_default")
        # Negative invariant — protect against stale defaults after refinement re-runs.
        # If the Planner re-ran and changed source from cli_flag_or_default to cli_flag
        # (or user_config), the previously-set default value would still be sitting here.
        if self.source is not InputSource.CLI_FLAG_OR_DEFAULT and self.default is not None:
            raise ValueError("default must be None when source is not cli_flag_or_default")
        return self
```

### 5.4 `LabResourceBlock`

```python
class LabResourceBlock(BaseModel):
    """schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    type: ValueTypeName
    intended_iac_resource_type: NonEmptyString  # e.g., "aws_s3_bucket"; from cloud catalog
    provisioning_mechanism: ProvisioningMechanism
    lab_role: list[LabRole] = Field(min_length=1)
    role_notes: dict[LabRole, NonEmptyString] = Field(default_factory=dict)
    description: ProvenanceString
    discovery: DiscoveryBlock | None = None
    # Free-form properties consumed by the Lab-level Generator; structurally validated only.
    properties: dict[str, Any] = Field(default_factory=dict)


class DiscoveryBlock(BaseModel):
    """Optional. schema.md §4.4."""
    model_config = ConfigDict(extra="forbid")

    shortcut_command: NonEmptyString
    attacker_command: NonEmptyString
```

### 5.5 `PhaseBlock`

```python
class PhaseBlock(BaseModel):
    """schema.md §4.5."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    name: NonEmptyString
    display_name: NonEmptyString
    short_description: NonEmptyString
    mitre_tactics: list[Annotated[str, StringConstraints(pattern=r"^TA\d{4}$")]] = Field(
        default_factory=list,
    )
    implements_chain_steps: list[KebabId] = Field(default_factory=list)
    step_composition: StepComposition
    execution_context: ExecutionContext
    on_dependency_failure: OnDependencyFailure = OnDependencyFailure.WARN
    bind_inputs: list[PhaseBindInput] = Field(default_factory=list)
    outputs: list[PhaseOutput] = Field(default_factory=list)
    produces_world_state: list[ProducesWorldState] = Field(default_factory=list)
    provisioning_mechanism: ProvisioningMechanism
    references_lab_outputs: list[NonEmptyString] = Field(default_factory=list)
    steps: list[StepBlock] = Field(min_length=1)
    implementation: PhaseImplementation
    extras: list[ExtrasEntry] = Field(default_factory=list)


class PhaseBindInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SnakeName
    type: ValueTypeName
    source_phase_output: NonEmptyString  # "phase-N.output_name"


class PhaseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SnakeName
    type: ValueTypeName


class ProducesWorldState(BaseModel):
    """schema.md §4.5. The identifier_kind discrimination is critical for cleanup correctness."""
    model_config = ConfigDict(extra="forbid")

    type: ValueTypeName
    identifier_kind: IdentifierKind
    identifier: NonEmptyString | None = None  # required for static
    identifier_source: NonEmptyString | None = None  # required for runtime_generated
    description: ProvenanceString

    @model_validator(mode="after")
    def _identifier_rules(self) -> "ProducesWorldState":
        if self.identifier_kind is IdentifierKind.STATIC:
            if not self.identifier or self.identifier_source:
                raise ValueError("static identifier_kind requires identifier, not identifier_source")
        else:  # RUNTIME_GENERATED
            if not self.identifier_source or self.identifier:
                raise ValueError(
                    "runtime_generated identifier_kind requires identifier_source, not identifier"
                )
        return self


class PhaseImplementation(BaseModel):
    """schema.md §4.5. ADR 0079: ``path`` is optional. The manifest is one
    incrementally-built model and Layer 1 runs after every stage, so the Planner
    emits a skeleton phase with no code and no path (``agents.md §5.7``); a
    required path would fail the Planner's own Layer-1 validation. Invariant:
    path <-> file. The Per-phase Generator (Phase 3) materializes the file and
    the path together via the ``id`` -> path derivation (``agents.md §9.4``);
    Layer 2 (post-generation) enforces ``path == derive(id)`` + file-exists."""
    model_config = ConfigDict(extra="forbid")

    language: Literal["python"]  # v1; widens in v1.5+
    path: NonEmptyString | None = None  # None pre-generation; set by the Generator
    entrypoint: SnakeName = "run_phase"
```

### 5.6 `StepBlock`

```python
class StepBlock(BaseModel):
    """schema.md §4.7."""
    model_config = ConfigDict(extra="forbid")

    id: KebabId
    step_number: int | str  # same int-or-"N.N" semantics as ChainStep
    title: NonEmptyString
    description: ProvenanceString
    function_name: SnakeName
    mitre_techniques: list[Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]] = Field(
        default_factory=list,
    )
    detections: list[DetectionBlock] = Field(default_factory=list)
    cli_equivalent: list[NonEmptyString] = Field(default_factory=list)
    # illustrative, not authoritative; Layer 2 does not verify equivalence per schema.md §4.7
    outputs: list[StepOutput] = Field(default_factory=list)
    tradecraft_notes: list[TradecraftNote] = Field(default_factory=list)
    extras: list[ExtrasEntry] = Field(default_factory=list)


class StepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SnakeName
    type: ValueTypeName
```

### 5.7 `OutputBlock`

```python
class OutputBlock(BaseModel):
    """schema.md §4.4. Lab-level outputs."""
    model_config = ConfigDict(extra="forbid")

    name: SnakeName
    type: ValueTypeName
    description: NonEmptyString | None = None
    # Either an IaC reference or a phase-output reference.
    iac_reference: NonEmptyString | None = None  # e.g., "terraform.output.bucket_name"
    phase_output_reference: NonEmptyString | None = None  # "phase-N.output_name"

    @model_validator(mode="after")
    def _source_xor(self) -> "OutputBlock":
        if bool(self.iac_reference) == bool(self.phase_output_reference):
            raise ValueError("exactly one of iac_reference or phase_output_reference must be set")
        return self
```

---

## 6. Registry meta-schemas

The registries are validated at load time against their meta-schemas. Each registry file is a YAML mapping `{ entries: [...] }` whose entries match the per-registry shape.

### 6.1 `value_types` meta-schema

```python
# cyberlab_gen/schemas/registries/value_types.py

class ValueTypeEntry(ArtifactModel):
    """schema.md §4.12."""

    name: SnakeName
    description: NonEmptyString
    schema_: dict[str, Any] = Field(alias="schema")  # JSON Schema for the value's shape
    sensitive: bool
    examples: list[Any] = Field(default_factory=list)
    notes_for_generator: NonEmptyString | None = None
    cleanup_metadata: NonEmptyString | None = None
    platforms: list[SnakeName] = Field(default_factory=list)
    # Proposal metadata, when this entry originated from a runtime proposal.
    proposed_by: Literal["extractor", "planner", "maintainer"] = "maintainer"
    proposed_in_run: str | None = None  # run-id reference; None for bundled entries


class ValueTypesRegistry(ArtifactModel):
    entries: list[ValueTypeEntry] = Field(default_factory=list)
```

### 6.2 `facets` meta-schema

```python
class FacetEntry(ArtifactModel):
    """schema.md §4.13."""

    name: FacetName
    category: Literal["target", "runtime", "lab_class_signal"]
    proposed_by: Literal["extractor", "planner", "maintainer"]
    description: NonEmptyString
    applies_at_levels: list[Literal["lab", "phase", "step"]] = Field(min_length=1)
    requires_fields: list[NonEmptyString] = Field(default_factory=list)
    implies: list[FacetName] = Field(default_factory=list)
    incompatible_with: list[FacetName] = Field(default_factory=list)
    examples: list[NonEmptyString] = Field(default_factory=list)
    first_class: bool = False  # only meaningful for runtime:* category
    notes_for_extractor: NonEmptyString | None = None
    notes_for_planner: NonEmptyString | None = None


class FacetsRegistry(ArtifactModel):
    entries: list[FacetEntry] = Field(default_factory=list)
```

### 6.3 `external_data_sources` and `static_catalogs` meta-schema

These share an entry shape with one optional-field difference (`enrichment_triggers` and `discrepancy_materiality_rules` only meaningful for `external_data_sources`).

```python
class ExternalSourceEndpoint(ArtifactModel):
    id: SnakeName
    method: Literal["GET", "POST"]
    path_template: str  # e.g., "/cves/{cve_id}" — may be "" when base_url is the full URL (RSS feeds, static catalogs). ADR 0007.
    parameters: dict[str, ExternalSourceParam] = Field(default_factory=dict)
    response_schema_ref: SnakeName | None = None  # named reference resolved by the adapter in cyberlab_gen/external_data_sources/
    cache_ttl: timedelta  # ISO 8601 duration in YAML (e.g., "P7D"); Pydantic parses to timedelta


class ExternalSourceParam(ArtifactModel):
    type: Literal["string", "integer", "boolean", "list_string"]
    required: bool = True
    description: NonEmptyString | None = None
    pattern: NonEmptyString | None = None  # optional regex for string params (e.g., "^CVE-[0-9]{4}-[0-9]+$")
    enum_values: list[NonEmptyString] | None = None  # optional allowed value list for string params (e.g., OSV ecosystem names)


class RateLimit(ArtifactModel):
    without_key: NonEmptyString | None = None  # e.g., "5 req/30s"
    with_key: NonEmptyString | None = None


class CacheConfig(ArtifactModel):
    ttl: timedelta  # ISO 8601 duration in YAML (e.g., "P7D"); Pydantic parses to timedelta
    scope: Literal["per-key", "global"] = "per-key"


class EnrichmentTrigger(ArtifactModel):
    """schema.md §4.14. Only on external_data_sources entries."""
    field: NonEmptyString  # JSONPath-like; e.g., "chain.chain_steps[*].cve_ids[*]"
    action: Literal["lookup"]  # v1: only lookup; widens in future
    endpoint: SnakeName  # endpoint id within this source


class DiscrepancyMaterialityRule(ArtifactModel):
    """schema.md §4.14. Only on external_data_sources entries."""
    field_path: NonEmptyString  # which field in the AttackSpec this rule applies to
    classification: Literal["material", "non_material"]
    rule_description: NonEmptyString


class _ExternalSourceEntryBase(ArtifactModel):
    """Internal base for the two external-source entry shapes. schema.md §4.14.

    Not used directly — split into ExternalDataSourceEntry and StaticCatalogEntry
    because the two registries have different semantic roles (the split lives at
    schema.md §4.14 line 645: enrichment_triggers belongs only to external_data_sources;
    notes_for_extractor vs notes_for_generator are addressed to different agents).
    """

    id: SnakeName
    name: NonEmptyString
    description: NonEmptyString
    base_url: HttpUrl
    auth_type: Literal["none", "optional_api_key", "required_api_key", "oauth"]
    auth_env_var: NonEmptyString | None = None
    rate_limit: RateLimit
    endpoints: list[ExternalSourceEndpoint] = Field(default_factory=list)
    cache: CacheConfig
    best_effort: bool = False

    @model_validator(mode="after")
    def _auth_rules(self) -> "_ExternalSourceEntryBase":
        if self.auth_type in ("required_api_key", "optional_api_key") and not self.auth_env_var:
            raise ValueError(f"auth_env_var required when auth_type is {self.auth_type}")
        return self


class ExternalDataSourceEntry(_ExternalSourceEntryBase):
    """schema.md §4.14. Entries in `external_data_sources` registry.

    These are called automatically during pre-Planner enrichment.
    Has enrichment_triggers + discrepancy_materiality_rules + notes_for_extractor.
    Does NOT have notes_for_generator (the Generator never reads this registry).
    """
    enrichment_triggers: list[EnrichmentTrigger] = Field(default_factory=list)
    discrepancy_materiality_rules: list[DiscrepancyMaterialityRule] = Field(default_factory=list)
    notes_for_extractor: NonEmptyString | None = None


class StaticCatalogEntry(_ExternalSourceEntryBase):
    """schema.md §4.14. Entries in `static_catalogs` registry.

    These are consulted on-demand by the Generator and Validator via
    lookup_cloud_iam_action(...) and analogous tools. Never enrichment-triggered.
    Has notes_for_generator. Does NOT have enrichment_triggers,
    discrepancy_materiality_rules, or notes_for_extractor.
    """
    notes_for_generator: NonEmptyString | None = None


class ExternalDataSourcesRegistry(ArtifactModel):
    entries: list[ExternalDataSourceEntry] = Field(default_factory=list)


class StaticCatalogsRegistry(ArtifactModel):
    entries: list[StaticCatalogEntry] = Field(default_factory=list)
```

The typed split is enforced structurally: a YAML entry under `static_catalogs:` with an `enrichment_triggers` field fails at parse time because `StaticCatalogEntry` rejects the unknown field (`extra="forbid"`). Same for a `notes_for_generator` under `external_data_sources:`. The two registries can no longer be confused for each other, and the misconfiguration mode that was previously silent now surfaces as a Layer 1 error.

### 6.4 `execution_contexts` meta-schema

```python
class ExecutionContextEntry(ArtifactModel):
    """schema.md §4.5, agents.md §5.10."""

    name: SnakeName
    description: NonEmptyString
    credential_assumption: NonEmptyString  # what's available re: credentials in this context; informs the Generator's prompt
    network_assumption: NonEmptyString     # what's available re: network in this context
    notes_for_generator: NonEmptyString | None = None  # additional generator-prompt guidance for this context
    typical_use_cases: list[NonEmptyString] = Field(default_factory=list)
    proposed_by: Literal["planner", "maintainer"] = "maintainer"


class ExecutionContextsRegistry(ArtifactModel):
    entries: list[ExecutionContextEntry] = Field(default_factory=list)
```

### 6.5 `lab_credentials` meta-schema

```python
class LabCredentialEntry(ArtifactModel):
    """schema.md §4.11. Canonical fake-credential patterns. Used by Generator and Validator Layer 5."""

    id: SnakeName  # e.g., "aws_canonical_access_key"
    platform: SnakeName  # "aws", "github", etc.
    description: NonEmptyString
    pattern: NonEmptyString  # regex or fixed string identifying canonical fakes
    example: NonEmptyString
    whitelist_rationale: NonEmptyString  # why Layer 5 may ignore this pattern


class LabCredentialsRegistry(ArtifactModel):
    entries: list[LabCredentialEntry] = Field(default_factory=list)
```

### 6.6 The merged-registries view

The runtime registry layer merges bundled (read-only) and overlay (writable) per `schema.md §4.11`. The merge logic and load surface:

```python
# cyberlab_gen/registries/loader.py

class MergedRegistries(BaseModel):
    """Loaded view of all registries: bundled + overlay merged."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    value_types: ValueTypesRegistry
    facets: FacetsRegistry
    external_data_sources: ExternalDataSourcesRegistry
    static_catalogs: StaticCatalogsRegistry
    execution_contexts: ExecutionContextsRegistry
    lab_credentials: LabCredentialsRegistry

    def value_type(self, name: str) -> ValueTypeEntry | None: ...
    def facet(self, name: str) -> FacetEntry | None: ...
    def external_source(self, source_id: str) -> ExternalDataSourceEntry | None: ...
    def static_catalog(self, catalog_id: str) -> StaticCatalogEntry | None: ...
    def execution_context(self, name: str) -> ExecutionContextEntry | None: ...
    def lab_credential_patterns(self, platform: str | None = None) -> list[LabCredentialEntry]: ...
```

Per `implementation-plan.md §3.2`: "Overlay wins on name collisions. Validation: every registry file conforms to its meta-schema."

#### Bundled vs overlay registry file shape

Bundled and overlay registry files both validate against the same per-registry shape (a `{entries: [...]}` mapping). The overlay file additionally carries an audit block keyed by entry name. Per `schema.md §4.16`:

```python
class ProposalAuditBlock(ArtifactModel):
    """Audit context for a runtime-proposed entry that landed in the overlay.

    Captures the proposal envelope (schema.md §4.16) — the metadata the framework
    recorded when the proposal was emitted. Preserved alongside the entry in the
    overlay file; stripped when the entry is promoted to bundled via maintainer PR
    (the audit context is preserved in git history instead).
    """

    proposal_origin: Literal["llm_during_extraction", "llm_during_planning"]
    source_lab: NonEmptyString          # lab id where the proposal originated
    source_blog: HttpUrl                # blog URL the lab was generated from
    proposed_by_model: NonEmptyString   # framework-recorded model id; not agent-authored
    proposed_at: datetime
    reasoning: NonEmptyString           # the agent's stated justification


class OverlayRegistryFile[E: BaseModel](ArtifactModel):
    """Shape of an overlay registry YAML file (one per registry).

    The generic parameter E is the registry's entry type (ValueTypeEntry,
    FacetEntry, etc.). The bundled file shape is the same minus the proposals
    block — a bundled file is just `{entries: [...]}`.

    The generic bound is `BaseModel` rather than `ArtifactModel` per ADR 0004
    reserved case 2 (generic-bound dispatch).
    """

    entries: list[E] = Field(default_factory=list)
    # Keyed by entry registry-key: `SnakeName` for five registries, `FacetName`
    # (`category:value`) for facets. Typed as `RegistryKey` (the union of the
    # two) so facet proposals are representable — a `SnakeName`-only key type
    # silently makes facet proposals impossible (the colon in a `FacetName`
    # fails the `SnakeName` pattern at parse time). See ADR 0015. The no-orphan
    # rule below still rejects any key with no matching entry, so the union does
    # not loosen the cross-registry guarantee. Entries without a proposals key
    # are maintainer-curated additions to the overlay (rare).
    proposals: dict[RegistryKey, ProposalAuditBlock] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _proposal_keys_match_entries(self) -> "OverlayRegistryFile[E]":
        entry_keys = {self._entry_key(e) for e in self.entries}
        for name in self.proposals:
            if name not in entry_keys:
                raise ValueError(
                    f"proposals[{name!r}] has no corresponding entry in `entries`"
                )
        return self

    @staticmethod
    def _entry_key(entry: BaseModel) -> str:
        """Resolve the registry key via each entry class's `ENTRY_KEY_FIELD`.

        Each entry type declares `ENTRY_KEY_FIELD: ClassVar[str]` naming its key
        field (`name` for most types, `id` for external-source and lab-credential
        entries). Reading it explicitly beats the older `getattr(entry, "name",
        None) or getattr(entry, "id")` chain: a missing declaration surfaces as a
        clear `AttributeError` rather than silently falling back, and a facet's
        key resolves to its full `FacetName` (e.g. `target:aws`).
        """
        key_field: str = getattr(type(entry), "ENTRY_KEY_FIELD")
        return getattr(entry, key_field)
```

Promotion from overlay to bundled drops the proposals block: the entry is copied to the bundled file's `entries:` list and the corresponding key under `proposals:` is removed. The bundled-file Pydantic shape is just `BundledRegistryFile[E]` with only an `entries: list[E]` field — no `proposals:` block, validated under `extra="forbid"` so a bundled file accidentally carrying proposal audit fails Layer 1.

### 6.7 External-source response schemas

The `ExternalSourceEndpoint.response_schema_ref` field is a `SnakeName` reference, not an inline JSON Schema. References resolve in the **source's adapter module**: for a source with `id: <source_id>`, the adapter lives at `cyberlab_gen/external_data_sources/<source_id>/` and registers its response schemas in `cyberlab_gen/external_data_sources/<source_id>/schemas.py`.

The adapter module exports a `RESPONSE_SCHEMAS: dict[SnakeName, dict[str, Any]]` mapping from ref name to the JSON Schema for that endpoint's response. The runtime layer resolves a `response_schema_ref: "nvd_cve_response_v2"` declaration on the registry entry by looking up `cyberlab_gen.external_data_sources.nvd.schemas.RESPONSE_SCHEMAS["nvd_cve_response_v2"]`.

This keeps the registry YAML focused on configuration (URLs, auth, enrichment triggers, materiality rules) and keeps response-parsing concerns with the code that does the parsing. The registry-vs-adapter boundary is enforced at load time: registry validation passes if `response_schema_ref` is a well-formed `SnakeName`; adapter validation (a separate step at runtime when an enrichment fires) checks that the referenced schema actually exists in the adapter's `RESPONSE_SCHEMAS` mapping. A missing schema raises an error that names both the registry entry and the expected adapter location, so the fix is unambiguous.

Adding a new external source means: register a new entry in `registry/external_data_sources.yaml` with its `response_schema_ref` declarations, plus create the corresponding adapter module under `cyberlab_gen/external_data_sources/<source_id>/`. The registry change without the adapter would fail at runtime; the adapter without the registry change would never be invoked. The two land together.

---

## 7. Field-by-field cross-reference table

This table is the cheat sheet the implementing agent uses when generating code from a schema reference. Every architectural mention of a field maps to a Pydantic shape here.

| Architecture reference | Pydantic model | Notes |
|---|---|---|
| `schema.md §4.4` envelope | `LabManifest` | top-level Manifest |
| `schema.md §4.4` core | `CoreBlock` | severity is `Provenance[Severity]`, not bare |
| `schema.md §4.4` lab_resources | `LabResourceBlock` | `lab_role` is `list[LabRole]`, multi-valued |
| `schema.md §4.5` phase | `PhaseBlock` | extras omitted-when-empty via `exclude_none` |
| `schema.md §4.5` produces_world_state | `ProducesWorldState` | identifier/identifier_source XOR enforced |
| `schema.md §4.6` step composition | `StepComposition` enum | sequential | independent |
| `schema.md §4.7` step | `StepBlock` | inside `PhaseBlock.steps` |
| `schema.md §4.7` detection block | `DetectionBlock` | shared by chain steps and manifest steps |
| `schema.md §4.8` envelope | `AttackSpec` | top-level AttackSpec |
| `schema.md §4.8` chain step | `ChainStep` | inside `ChainBlock.chain_steps` |
| `schema.md §4.8` thesis | `ThesisBlock` | `types` is multi-value list of `ThesisType` |
| `schema.md §4.8` real_world_incidents | `RealWorldIncidentsBlock` | tri-state status with rules |
| `schema.md §4.8` reproducibility (lab) | `ReproducibilityBlock` | derived; not authored |
| `schema.md §4.8` material_discrepancies | `MaterialDiscrepancy` | top-level index; framework-written by enrichment, never an agent; see §4.9 / ADR 0017 |
| `schema.md §4.9` provenance | `Provenance[T]` | generic envelope, every content field |
| `schema.md §4.9` citations | `CitationBlock` | kind-discriminated |
| `schema.md §4.10` extras | `ExtrasEntry` | escape hatch at four levels |
| `schema.md §4.12` value_types | `ValueTypeEntry` | registry entry |
| `schema.md §4.13` facets | `FacetEntry` | category-discriminated |
| `schema.md §4.14` external_data_sources | `ExternalDataSourceEntry` | has `enrichment_triggers` + `discrepancy_materiality_rules` + `notes_for_extractor` |
| `schema.md §4.14` static_catalogs | `StaticCatalogEntry` | omits `enrichment_triggers`, `discrepancy_materiality_rules`, `notes_for_extractor`; has `notes_for_generator` |
| `agents.md §5.10` execution contexts | `ExecutionContextEntry` | registry entry |

---

## 8. JSON Schema export

Layer 1 validation (`validation.md §6.4`) uses the JSON Schemas generated from these Pydantic models. The export convention:

- `AttackSpec.model_json_schema()` produces `attackspec.schema.json`.
- `LabManifest.model_json_schema()` produces `manifest.schema.json`.
- Each registry meta-schema exports to `<registry-name>.schema.json`.
- Exports happen at build time and check into `cyberlab_gen/schemas/json/`. They are NOT regenerated at runtime; the static export is what Layer 1 validates against. Drift between Pydantic and JSON Schema is a CI failure (a build-step test re-exports and diffs).

This keeps Layer 1 fast (no Pydantic-load overhead per validation) and lets non-Python consumers (the eval harness's external scripts, future TypeScript tooling) read the schemas directly.

---

## 9. What this document deliberately does not specify

These are out of scope for `schema-details.md` and live elsewhere when they exist:

- **Exact prompt text** that instructs the Extractor to populate these shapes. Lives in `prompts.md` (Phase 1 deliverable per `implementation-plan.md §8.6`).
- **Layer-specific validation rules** (severity floors, what counts as a Layer 2 finding). Lives in `validator-rules.md` (Phase 3 deliverable).
- **Registry seed contents.** Lives in `registry-details.md` (the companion document).
- **Migration logic between schema versions.** Per `architecture.md §0.6`: no migration in v1. Old artifacts produce a "regenerate from blog URL" message.
- **Performance tuning.** Pydantic v2 is fast enough; profile if needed.

---

## 10. Follow-up changes outside this document — completed

Two changes consistent with the shapes in this document needed to land in the architecture layer. Both have been completed; this section preserves the record of why they were necessary and what was done.

### 10.1 Strike the `actor_scoped` paragraph in `schema.md §4.13` — done

`schema.md §4.13` previously reserved `actor_scoped:defender` and `actor_scoped:observer` as namespace for v1.5+ defender-mode and observer-mode labs. This document removes those values from `FacetName` and from `FacetEntry.category` because v1 has no code that consumes them. The corresponding paragraph in `schema.md §4.13` has been replaced with a v1.5+ deferral note that does not reserve namespace, per the no-migration discipline (`architecture.md §0.6`). Reintroducing the category in v1.5+ alongside its consuming code is a clean schema-version bump, not a regression.

### 10.2 Specify `requires_user_confirmation` semantics in `schema.md §4.9` — done

`schema.md §4.9` now specifies the flag's semantics: framework-set (not agent-set), set when `source == llm_inference` with `confidence` below the user-confirmation threshold OR when the framework's enrichment/cross-check identifies the field as decision-shaping AND uncertain, surfaced at the post-Extractor and post-Planner interrupts as a per-field review surface. The calibration item has been added to `architecture.md §8.4` ("User-confirmation confidence threshold," placeholder 0.6).

The `Provenance._source_rules` validator does not yet enforce the framework-only-authorship rule mechanically (the framework distinguishes itself from agents at the call-site layer, not at the schema layer). The TODO comment in the Provenance code referencing this remains as documentation of the limitation; closing it would require either the framework to populate a marker field on the provenance, or the validator to be context-aware (neither is structurally clean at the schema layer).

---

*End of schema details. The Pydantic shapes here are what get checked into `cyberlab_gen/schemas/`. Cross-references back to `schema.md` are preserved as docstring comments in the implementing code.*
