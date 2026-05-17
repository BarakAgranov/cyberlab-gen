"""Closed enums from the architecture.

Architectural source: ``schema-details.md`` §2.2. Each enum's docstring cites
the ``schema.md`` section that owns its semantics. Extending a closed enum is
a maintainer PR, not a runtime concern (``schema.md`` §4.7).

``MessageRole`` deliberately lives in ``cyberlab_gen.providers.base`` per
``provider-interface.md`` §4.1 and is therefore not part of this module, even
though the phase-0 brief's example list mentions it.
"""

from enum import StrEnum


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
    """How a confidence value was produced. schema.md §4.9.

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


__all__ = [
    "CitationKind",
    "ConfidenceSource",
    "DefenderTechniqueKind",
    "DefenseApplicability",
    "DetectionComponent",
    "DetectionFormat",
    "ExtractionOutcome",
    "IdentifierKind",
    "IncidentStatus",
    "InputSource",
    "LabRole",
    "OnDependencyFailure",
    "PrereqKind",
    "PrereqTiming",
    "ProvenanceSource",
    "ProvisioningMechanism",
    "PublisherKind",
    "ReproducibilityLabLevel",
    "ReproducibilityTier",
    "Severity",
    "SpecKind",
    "StepComposition",
]
