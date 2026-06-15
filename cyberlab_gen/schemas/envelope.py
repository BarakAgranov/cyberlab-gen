"""Shared versioned-artifact envelope. ADR 0080.

Architectural source: ``architecture.md §1.5`` (the framework owns versioning),
``§0.6`` (refuse to load old-schema artifacts; never migrate). The base for the
framework's versioned, load-gated artifacts (``AttackSpec``, ``LabManifest``).
"""

from typing import ClassVar

from pydantic import Field

from cyberlab_gen.schemas.base import ArtifactModel


class SpecEnvelope(ArtifactModel):
    """Base for the framework's versioned, load-gated artifacts. ADR 0080.

    Carries the *versioning + load-gate identity*: ``spec_version`` (the
    framework-owned schema version — the LLM never authors it, ADR 0069) and the
    per-kind ``CURRENT_VERSION`` contract. Each spec kind sets its own
    ``CURRENT_VERSION``, because the two artifacts evolve independently: a manifest
    field addition must not invalidate every on-disk AttackSpec (so the versions
    are per-kind, amending ADR 0069's single constant).

    ``spec_kind`` (the discriminator the load gate dispatches on) is declared by
    each subclass as a fixed ``Literal`` rather than as a narrowing override of a
    base field — precise per-kind typing, and it avoids an unsound mutable-attribute
    override. ``schemas.loading.load_spec`` reads ``spec_kind`` off the parsed mapping.

    ``source`` is deliberately **not** here: it is blog provenance (top-level on
    ``AttackSpec``, nested in ``CoreBlock`` on ``LabManifest``), not part of being a
    versioned artifact, and hoisting it would over-constrain a future
    non-blog-derived spec. The flat-vs-grouped asymmetry between the two artifacts
    is two independently reasonable choices, not an accident. If a cross-artifact
    ``source`` consumer ever appears, add a typed ``artifact_source(spec)`` accessor
    at that point — do not reshape the artifacts and do not pre-build the accessor
    now (no second use yet).
    """

    #: The schema version the framework stamps for this spec kind. Each subclass sets it.
    CURRENT_VERSION: ClassVar[int]

    spec_version: int = Field(ge=1)
