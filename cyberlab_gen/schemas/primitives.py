"""Constrained string primitives shared across the schema layer.

Architectural source: ``schema-details.md`` §2.1.

These aliases name the patterns the architecture commits to for identifiers,
versions, and content hashes. Every model that wants a "kebab-case id",
"snake_case name", "facet name", "tradecraft name", "semver string", or
"SHA-256 hex digest" reuses these aliases rather than restating the regex.

``HttpUrl`` is re-exported from Pydantic for the same reason — keeping a
single canonical URL type across the schema layer.
"""

from typing import Annotated

from pydantic import HttpUrl, StringConstraints

KebabId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        min_length=1,
        max_length=128,
    ),
]
"""Stable identifier strings (lowercase, kebab-case). schema-details.md §2.1."""

SnakeName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*$",
        min_length=1,
        max_length=128,
    ),
]
"""value_type and facet base names (snake_case). schema-details.md §2.1."""

FacetName = Annotated[
    str,
    StringConstraints(
        pattern=r"^(target|runtime|lab_class_signal):[a-z][a-z0-9_]*$",
        min_length=3,
        max_length=128,
    ),
]
"""Facet names following ``category:name`` shape. schema-details.md §2.1."""

TradecraftName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*:[a-z0-9]+(-[a-z0-9]+)*$",
        min_length=3,
        max_length=128,
    ),
]
"""Tradecraft names following ``prefix:kebab-name``. schema-details.md §2.1; schema.md §4.7."""

NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
"""String with at least one character. schema-details.md §2.1."""

ThesisType = SnakeName
"""Open-set thesis-type name (snake_case). schema-details.md §2.3.

Validated against the thesis-types catalog at Layer 1 rather than as a Pydantic
enum constraint, because the valid set depends on which registries are loaded
(ADR 0016). Structurally it is just a ``SnakeName``.
"""

ExternalDataSourceId = SnakeName
"""Open-set external-data-source id (snake_case). schema-details.md §2.3.

Validated against the ``external_data_sources`` registry at Layer 1. Structurally
just a ``SnakeName`` (e.g. ``nvd``, ``mitre_attack``).
"""

SemVer = Annotated[
    str,
    StringConstraints(
        pattern=r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$",
    ),
]
"""Semver-style version strings. schema-details.md §2.1."""

Sha256Hex = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-f0-9]{64}$",
    ),
]
"""SHA-256 hex digest (lowercase). schema-details.md §2.1."""

MitreTechniqueId = Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]
"""MITRE ATT&CK technique id, e.g. ``T1059`` or ``T1059.001``. schema.md §4.7.

Structural identifier (not content), so a bare string — validated against the
bundled MITRE catalog at enrichment time (``pipeline.md §3.2.4``), not as a
registry-enum constraint.
"""

MitreTacticId = Annotated[str, StringConstraints(pattern=r"^TA\d{4}$")]
"""MITRE ATT&CK tactic id, e.g. ``TA0001``. schema-details.md §4.5."""

CveId = Annotated[str, StringConstraints(pattern=r"^CVE-\d{4}-\d{4,}$")]
"""CVE identifier, e.g. ``CVE-2021-44228``. schema-details.md §4.5."""

RegistryKey = SnakeName | FacetName
"""Union of the two registry-key shapes. schema-details.md §6.6; ADR 0015.

Most registry entries key by ``SnakeName`` (value_types, execution_contexts,
external_data_sources, static_catalogs, lab_credentials). The ``facets``
registry keys by ``FacetName`` (``category:value``, e.g. ``target:aws``). A
container keyed on entry-key -- notably ``OverlayRegistryFile.proposals`` --
must admit both, or facet proposals are structurally impossible: the colon in
a ``FacetName`` fails the ``SnakeName`` pattern at parse time. This is not a
loosening. ``OverlayRegistryFile._proposal_keys_match_entries`` still rejects
any key with no matching entry, so a value_types overlay carrying a
facet-shaped proposal key fails on the no-corresponding-entry rule.
"""

__all__ = [
    "CveId",
    "ExternalDataSourceId",
    "FacetName",
    "HttpUrl",
    "KebabId",
    "MitreTacticId",
    "MitreTechniqueId",
    "NonEmptyString",
    "RegistryKey",
    "SemVer",
    "Sha256Hex",
    "SnakeName",
    "ThesisType",
    "TradecraftName",
]
