"""Pydantic v2 base classes for the schema layer.

Architectural source: ``schema-details.md`` §1.

``ArtifactModel`` is the base for everything that gets serialized as a final
artifact (AttackSpec, LabManifest, registry files). ``extra="forbid"`` is
critical: unknown fields in user-edited artifacts (post-interrupt edits) must
surface as Layer 1 validation errors, not be silently dropped.

``InternalModel`` is the base for internal-only structures that don't cross
the artifact boundary. ``extra="ignore"`` because internal types evolve more
freely.
"""

from pydantic import BaseModel, ConfigDict


class ArtifactModel(BaseModel):
    """Base for everything serialized as a final artifact.

    ``extra="forbid"`` ensures unknown fields surface as Layer 1 validation
    errors rather than being silently dropped. ``validate_assignment=True``
    extends validation to post-construction mutation, so editing a model
    in-place can't smuggle in invalid values. ``str_strip_whitespace=True``
    normalizes incoming strings.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class InternalModel(BaseModel):
    """Base for internal-only structures that never cross the artifact boundary.

    ``extra="ignore"`` because internal types evolve more freely than artifacts.
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_assignment=False,
    )
