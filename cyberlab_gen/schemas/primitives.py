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
    "FacetName",
    "HttpUrl",
    "KebabId",
    "NonEmptyString",
    "RegistryKey",
    "SemVer",
    "Sha256Hex",
    "SnakeName",
    "TradecraftName",
]
