"""Load gate: dispatch a parsed spec mapping on ``spec_kind`` and version-gate it.

ADR 0069 / 0080; ``architecture.md §0.6`` (old-schema artifacts are refused at load,
never migrated). This is the single home for "load a spec from disk": the ``extract``
post-interrupt edit, and the future ``validate`` / ``fix`` / ``plan`` verbs, all route
through it so the per-kind version gate lives in exactly one place.
"""

from typing import cast

from cyberlab_gen.errors import SpecVersionError
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.enums import SpecKind
from cyberlab_gen.schemas.envelope import SpecEnvelope
from cyberlab_gen.schemas.manifest import LabManifest

_SPEC_BY_KIND: dict[SpecKind, type[SpecEnvelope]] = {
    SpecKind.ATTACK_SPEC: AttackSpec,
    SpecKind.LAB_MANIFEST: LabManifest,
}


def load_spec(data: object) -> SpecEnvelope:
    """Validate a parsed spec mapping, dispatching on ``spec_kind``.

    Reads ``spec_kind`` to pick the concrete model (``AttackSpec`` / ``LabManifest``),
    structurally validates the mapping against it, then refuses any spec whose
    ``spec_version`` differs from that kind's ``CURRENT_VERSION`` — old-schema artifacts
    are rejected, never migrated (``architecture.md §0.6``). Raises ``ValueError`` on a
    non-mapping or unknown ``spec_kind``, ``pydantic.ValidationError`` on a structurally
    invalid spec, and ``SpecVersionError`` on a version mismatch.
    """
    if not isinstance(data, dict):
        raise ValueError("spec must be a mapping carrying a spec_kind")
    mapping = cast("dict[str, object]", data)
    kind_raw = mapping.get("spec_kind")
    try:
        kind = SpecKind(kind_raw)
    except ValueError:
        raise ValueError(f"unknown or missing spec_kind: {kind_raw!r}") from None
    spec = _SPEC_BY_KIND[kind].model_validate(mapping)
    if spec.spec_version != type(spec).CURRENT_VERSION:
        raise SpecVersionError(found=spec.spec_version, expected=type(spec).CURRENT_VERSION)
    return spec
