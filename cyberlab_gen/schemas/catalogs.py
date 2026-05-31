"""Pydantic shapes for the closed bundled-only catalogs.

Architectural source: ``registry-details.md`` §7 (the closed bundled-only
catalogs); ``schema.md`` §4.5 / §4.7 (where the enum-valued fields these
catalogs back are consumed); ADR 0016.

These are *not* registries in the proposal-flow sense (``registry-details.md``
§1): they are enumerated closed sets the architecture pins, plus -- for
``thesis_types`` -- one open-set-in-spirit catalog that grows only by
maintainer PR. They never participate in the runtime proposal lifecycle, so
they live here rather than in ``registries.py``, and they are deliberately
*not* part of ``MergedRegistries`` (``schema-details.md §6.6``): the merged
view holds the six runtime-consulted registries, while these catalogs carry
display/ordinal/extension/validator-support metadata consulted on demand by
specific consumers (Layer 3 reads ``ordinal``; the Generator reads
``validator_support``; the Docs Generator reads ``display_name``).

The catalog *membership* for the four closed enums is owned by the
corresponding ``StrEnum`` in ``cyberlab_gen.schemas.enums`` -- that enum is what
validates the field on the artifact models. The entry models below key on
those same enum members (so the YAML cannot name a value the enum doesn't
know) and add only the metadata the enum cannot hold. ``thesis_types`` has no
enum (it is open-set per §1) and so keys on a plain ``SnakeName``.
"""

from typing import Literal

from pydantic import Field

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import (
    DetectionComponent,
    DetectionFormat,
    ProvisioningMechanism,
    Severity,
)
from cyberlab_gen.schemas.primitives import NonEmptyString, SnakeName

# --- Entry shapes ----------------------------------------------------------


class DetectionComponentEntry(ArtifactModel):
    """One entry in the ``detection_components`` catalog. ``registry-details.md §7.1``.

    ``name`` is a ``DetectionComponent`` enum member: the enum owns membership
    (a YAML value the enum doesn't know fails here), and this entry adds the
    human-facing ``display_name`` / ``description`` the enum can't carry.
    """

    name: DetectionComponent
    display_name: NonEmptyString
    description: NonEmptyString


class SeverityLevelEntry(ArtifactModel):
    """One entry in the ``severity_levels`` catalog. ``registry-details.md §7.2``.

    Adds the ``ordinal`` (4=Critical ... 1=Low) used for cross-severity
    comparison in the validator's Layer 3 severity-floor rules. ``name`` is a
    ``Severity`` enum member.
    """

    name: Severity
    ordinal: int = Field(ge=1, le=4)


class DetectionFormatEntry(ArtifactModel):
    """One entry in the ``detection_formats`` catalog. ``registry-details.md §7.3``.

    Adds ``display_name`` / ``file_extension`` / ``description``. ``name`` is a
    ``DetectionFormat`` enum member.
    """

    name: DetectionFormat
    display_name: NonEmptyString
    file_extension: NonEmptyString
    description: NonEmptyString


class ProvisioningMechanismEntry(ArtifactModel):
    """One entry in the ``provisioning_mechanisms`` catalog. ``registry-details.md §7.4``.

    Adds ``display_name`` / ``description`` and the ``validator_support`` tier
    the Generator and Validator consult when choosing a mechanism per §4.20.
    ``name`` is a ``ProvisioningMechanism`` enum member.
    """

    name: ProvisioningMechanism
    display_name: NonEmptyString
    description: NonEmptyString
    validator_support: Literal["full", "partial", "minimal", "none", "per-resource"]


class ThesisTypeEntry(ArtifactModel):
    """One entry in the ``thesis_types`` catalog. ``registry-details.md §7.6``.

    Unlike the four catalogs above, ``thesis_types`` is open-set in spirit
    (``registry-details.md §1``): it has no closed enum and grows by maintainer
    PR informed by telemetry. ``name`` is therefore a plain ``SnakeName``, not
    an enum member. A thesis may carry multiple types (``schema.md §4.8``).
    """

    name: SnakeName
    description: NonEmptyString


# --- Per-catalog containers (`{entries: [...]}` shape) ---------------------


class DetectionComponentsCatalog(ArtifactModel):
    """The ``detection_components`` catalog as a list of entries."""

    entries: list[DetectionComponentEntry] = Field(default_factory=list[DetectionComponentEntry])


class SeverityLevelsCatalog(ArtifactModel):
    """The ``severity_levels`` catalog as a list of entries."""

    entries: list[SeverityLevelEntry] = Field(default_factory=list[SeverityLevelEntry])


class DetectionFormatsCatalog(ArtifactModel):
    """The ``detection_formats`` catalog as a list of entries."""

    entries: list[DetectionFormatEntry] = Field(default_factory=list[DetectionFormatEntry])


class ProvisioningMechanismsCatalog(ArtifactModel):
    """The ``provisioning_mechanisms`` catalog as a list of entries."""

    entries: list[ProvisioningMechanismEntry] = Field(
        default_factory=list[ProvisioningMechanismEntry]
    )


class ThesisTypesCatalog(ArtifactModel):
    """The ``thesis_types`` catalog as a list of entries."""

    entries: list[ThesisTypeEntry] = Field(default_factory=list[ThesisTypeEntry])
