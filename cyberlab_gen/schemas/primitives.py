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

__all__ = [
    "FacetName",
    "HttpUrl",
    "KebabId",
    "NonEmptyString",
    "SemVer",
    "Sha256Hex",
    "SnakeName",
    "TradecraftName",
]
