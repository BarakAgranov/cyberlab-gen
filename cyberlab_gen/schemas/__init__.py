"""Schemas subpackage - Pydantic v2 artifact and registry models.

Defines ``ArtifactModel`` / ``InternalModel`` base classes, primitives, enums,
provenance, AttackSpec / LabManifest envelopes, and registry meta-schemas.
Architectural source: ``docs/schema.md`` and ``docs/schema-details.md``.
Phase 0 ships the base layer and AttackSpec envelope (Tasks 1-3).

Cross-subpackage imports go through this re-export surface; intra-package
modules import from each sibling module directly.
"""

from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.enums import (
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
from cyberlab_gen.schemas.primitives import (
    FacetName,
    HttpUrl,
    KebabId,
    NonEmptyString,
    SemVer,
    Sha256Hex,
    SnakeName,
    TradecraftName,
)

__all__ = [
    "ArtifactModel",
    "CitationKind",
    "ConfidenceSource",
    "DefenderTechniqueKind",
    "DefenseApplicability",
    "DetectionComponent",
    "DetectionFormat",
    "ExtractionOutcome",
    "FacetName",
    "HttpUrl",
    "IdentifierKind",
    "IncidentStatus",
    "InputSource",
    "InternalModel",
    "KebabId",
    "LabRole",
    "NonEmptyString",
    "OnDependencyFailure",
    "PrereqKind",
    "PrereqTiming",
    "ProvenanceSource",
    "ProvisioningMechanism",
    "PublisherKind",
    "ReproducibilityLabLevel",
    "ReproducibilityTier",
    "SemVer",
    "Severity",
    "Sha256Hex",
    "SnakeName",
    "SpecKind",
    "StepComposition",
    "TradecraftName",
]
