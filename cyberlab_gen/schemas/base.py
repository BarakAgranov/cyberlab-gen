"""Pydantic v2 base classes for the schema layer.

Architectural source: ``schema-details.md`` §1.

``ArtifactModel`` is the base for everything that gets serialized as a final
artifact (AttackSpec, LabManifest, registry files). ``extra="forbid"`` is
critical: unknown fields in user-edited artifacts (post-interrupt edits) must
surface as Layer 1 validation errors, not be silently dropped.

``InternalModel`` is the base for internal-only structures that don't cross
the artifact boundary. ``extra="ignore"`` because internal types evolve more
freely.

``ArtifactModel`` also carries the YAML round-trip surface (``to_yaml`` /
``from_yaml``) per ``schema-details.md`` §1: every artifact is editable as
a YAML file, so every artifact must serialize and deserialize losslessly.
"""

from io import StringIO
from typing import Self

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

    def to_yaml(self) -> str:
        """Serialize to YAML via ``ruamel.yaml``.

        Uses ``model_dump(mode="json", by_alias=True)`` so enum members
        serialize to their string values, other Pydantic-managed types
        (URLs, datetimes) become YAML-friendly scalars, and fields with a
        ``Field(alias=...)`` declaration round-trip under the alias the
        user-facing YAML actually uses (e.g., ``schema:`` rather than the
        Python attribute ``schema_:``). ``populate_by_name=True`` on the
        config keeps both names accepted on parse. Block-style output (no
        flow); key order follows the Pydantic field declaration order.
        """
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(self.model_dump(mode="json", by_alias=True), stream)
        return stream.getvalue()

    @classmethod
    def from_yaml(cls, raw: str) -> Self:
        """Parse YAML text into a validated model instance.

        Round-trips with ``to_yaml``: ``cls.from_yaml(instance.to_yaml())``
        equals ``instance`` when the model is composed only of YAML-safe
        primitives (which all artifact models are by design).
        """
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(raw)
        return cls.model_validate(data)


class InternalModel(BaseModel):
    """Base for internal-only structures that never cross the artifact boundary.

    ``extra="ignore"`` because internal types evolve more freely than artifacts.
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_assignment=False,
    )
