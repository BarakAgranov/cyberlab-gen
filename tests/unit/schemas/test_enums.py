"""Tests for the closed StrEnums in ``schemas/enums.py``.

Architectural source: ``schema-details.md`` §2.2. Each enum's value set is a
contract; the tests below pin every member's string value so accidental
renames, additions, or removals fail loudly.
"""

from enum import StrEnum

from cyberlab_gen.schemas import (
    ArtifactModel,
    CitationKind,
    ConfidenceSource,
    DefenderTechniqueKind,
    DefenseApplicability,
    DetectionComponent,
    DetectionFormat,
    ExtractionOutcome,
    IdentifierKind,
    IncidentStatus,
    InputSource,
    LabRole,
    OnDependencyFailure,
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


def _values(enum_cls: type[StrEnum]) -> set[str]:
    return {member.value for member in enum_cls}


def test_spec_kind_values() -> None:
    assert _values(SpecKind) == {"AttackSpec", "LabManifest"}


def test_provenance_source_values() -> None:
    assert _values(ProvenanceSource) == {
        "blog_explicit",
        "external_api",
        "llm_inference",
        "unknown_from_blog",
        "user_provided",
    }


def test_confidence_source_values() -> None:
    assert _values(ConfidenceSource) == {"framework_computed", "model_self_reported"}


def test_citation_kind_values() -> None:
    assert _values(CitationKind) == {
        "blog_passage",
        "external_api_response",
        "llm_reasoning_trace",
        "user_input",
    }


def test_severity_values() -> None:
    assert _values(Severity) == {"Critical", "High", "Medium", "Low"}


def test_detection_component_values() -> None:
    assert _values(DetectionComponent) == {
        "CSPM",
        "CWP",
        "CDR",
        "CIEM",
        "DSPM",
        "ASPM",
        "ITDR",
        "KSPM",
        "API_Security",
        "Supply_Chain_Security",
    }


def test_detection_format_values() -> None:
    assert _values(DetectionFormat) == {"sigma", "kql", "spl", "esql"}


def test_provisioning_mechanism_values() -> None:
    assert _values(ProvisioningMechanism) == {
        "terraform",
        "cloudformation",
        "arm_template",
        "gcp_deployment_manager",
        "cli_scripts",
        "manual",
        "mixed",
    }


def test_step_composition_values() -> None:
    assert _values(StepComposition) == {"sequential", "independent"}


def test_identifier_kind_values() -> None:
    assert _values(IdentifierKind) == {"static", "runtime_generated"}


def test_on_dependency_failure_values() -> None:
    assert _values(OnDependencyFailure) == {"warn", "fail", "skip"}


def test_lab_role_values() -> None:
    assert _values(LabRole) == {
        "attack_target",
        "attacker_infrastructure",
        "defender_infrastructure",
        "neutral",
    }


def test_reproducibility_tier_values() -> None:
    assert _values(ReproducibilityTier) == {
        "full",
        "partial_simulation",
        "demonstration_only",
        "not_reproducible",
    }


def test_reproducibility_lab_level_values() -> None:
    assert _values(ReproducibilityLabLevel) == {
        "full",
        "partial_simulation",
        "demonstration_only",
        "not_reproducible",
        "mixed",
    }


def test_extraction_outcome_values() -> None:
    assert _values(ExtractionOutcome) == {"in_scope", "out_of_scope"}


def test_prereq_kind_values() -> None:
    assert _values(PrereqKind) == {"manual", "auto_fixable", "automatic"}


def test_prereq_timing_values() -> None:
    assert _values(PrereqTiming) == {"pre_lab", "mid_lab"}


def test_input_source_values() -> None:
    assert _values(InputSource) == {"user_config", "cli_flag", "cli_flag_or_default"}


def test_publisher_kind_values() -> None:
    assert _values(PublisherKind) == {
        "vendor_lab",
        "researcher_personal",
        "vendor_advisory",
        "conference_writeup",
        "other",
    }


def test_incident_status_values() -> None:
    assert _values(IncidentStatus) == {"unknown", "none_observed", "incidents_documented"}


def test_defender_technique_kind_values() -> None:
    assert _values(DefenderTechniqueKind) == {
        "investigation",
        "detection_engineering",
        "threat_hunting",
        "forensic_analysis",
    }


def test_defense_applicability_values() -> None:
    assert _values(DefenseApplicability) == {
        "customer_actionable",
        "architectural_mitigation",
        "detection_only",
        "vendor_only",
    }


class _SeverityHolder(ArtifactModel):
    severity: Severity


def test_severity_round_trips_through_artifact_model() -> None:
    """Confirms use_enum_values=False keeps the enum member through dump/validate.

    schema-details.md §1 fixes use_enum_values=False so consumers get the
    enum member back rather than the raw string.
    """
    original = _SeverityHolder(severity=Severity.HIGH)
    dumped = original.model_dump()
    assert dumped == {"severity": Severity.HIGH}
    rehydrated = _SeverityHolder.model_validate({"severity": "High"})
    assert rehydrated.severity is Severity.HIGH
