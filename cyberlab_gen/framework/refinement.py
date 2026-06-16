"""Targeted-patch refinement: the convergent alternative to blind re-extraction.

Architectural source: ``architecture.md §1.7`` (refinement is a targeted patch, not a
full re-run), ``schema.md §4.9`` ("Refinement addressing: field paths and patches"),
ADR 0048 (the decision), ADR 0054 (this mechanism).

On a jury ``revise`` the responsible agent is handed the prior ``AttackSpec`` plus the
structured findings and emits a :class:`RefinementPatch` — new ``{value, source,
citations, …}`` sub-trees for **only** the flagged field paths. :func:`apply_field_patch`
deep-sets each patch onto a copy of the prior spec and re-validates the **whole** spec via
``AttackSpec.model_validate``. Because only flagged paths are written, every unflagged
field — value *and* inline provenance — stays byte-identical to the prior spec, so
refinement is **convergent by construction**: a patch cannot regress a field nobody
flagged (the failure mode of re-rolling every field, the 9→6→9→10 quality bounce).

This module is **pure framework** — it depends only on the schema layer, never on a
provider — so the convergence property is unit-testable without spending money. The agent
that *emits* the patch (the Extractor's ``refine``) lives in the agents layer; the
deterministic apply-and-revalidate is here, per the ``architecture.md §1.5`` split.

**Path convention (Phase 1):** dotted segments for object fields, ``[i]`` integer index for
list elements — e.g. ``chain.chain_steps[0].description`` — matching ``GapEntry`` /
``MaterialDiscrepancy``'s locator convention and the jury's field paths. A non-integer
``[id]`` segment is rejected (:class:`RefinementPathError`), not silently resolved; the
broader producer-convention drift (the static-schema validator's ``[step-1]`` ids) is
canonicalized under the A3/B1 "one findings set" pass, not here.
"""

from __future__ import annotations

import logging
import re
from typing import cast

from pydantic import JsonValue

from cyberlab_gen.errors import CyberlabGenError
from cyberlab_gen.framework.provenance_guard import (
    framework_owned_path_buckets,
    neutralize_patch_provenance,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel

logger = logging.getLogger(__name__)


class RefinementPathError(CyberlabGenError):
    """A patch ``field_path`` did not resolve onto the prior spec (ADR 0054).

    Raised when a path is malformed, names a field/index that is absent from the prior
    spec, or uses a non-integer ``[id]`` index. The refinement caller treats this like a
    re-validation failure: a bounded re-prompt with the offending path, never a silent
    create-at-the-wrong-place (the invented-path guard).
    """


class FieldPatch(InternalModel):
    """One field-targeted patch: a path plus its corrected sub-tree.

    ``new_value`` is a raw JSON value (``pydantic.JsonValue``) rather than a statically
    typed sub-tree because the type at a path varies (``Provenance[str]``,
    ``Provenance[Severity]``, a whole ``ChainStep``, a bare facet string…). The strict
    shape is recovered when :func:`apply_field_patch` re-validates the assembled spec via
    ``AttackSpec.model_validate``; a mis-shaped ``new_value`` surfaces there as a normal
    validation error (the agent then re-prompts). For a content field, ``new_value`` is the
    **whole** ``Provenance`` object (``{value, source, citations, …}``), so a patch updates
    a field's content *and* its provenance together.
    """

    field_path: str
    new_value: JsonValue


class RefinementPatch(InternalModel):
    """The Extractor/Planner's refinement emit: patches for only the flagged field paths.

    Small by design — typically the 1-3 fields a jury ``revise`` named — which also keeps
    the forced emit far under the truncation ceiling that bounds a full-``AttackSpec`` emit
    (ADR 0032), the opposite trade-off from re-extraction.
    """

    patches: list[FieldPatch]


def apply_field_patch(prior: AttackSpec, patch: RefinementPatch) -> AttackSpec:
    """Deep-set ``patch`` onto a copy of ``prior`` and re-validate the whole spec.

    Dumps ``prior`` to its JSON form (the lossless ``model_dump`` ↔ ``model_validate``
    round-trip the ``to_yaml`` / ``from_yaml`` surface already relies on), writes each
    patch's ``new_value`` at its ``field_path``, then re-validates the **entire** assembled
    dict via ``AttackSpec.model_validate`` — so every cross-field invariant (provenance
    rules, scope consistency, monotonic step numbers, ``extra="forbid"``) is enforced, not
    just the patched fields (requirement R2). ``prior`` is never mutated (the dump is a
    fresh dict).

    Raises :class:`RefinementPathError` when a path doesn't resolve onto the prior spec,
    and ``pydantic.ValidationError`` when the assembled spec is invalid — the caller
    (``Extractor.refine``) catches both and re-prompts within a bounded budget (R1).
    """
    data: dict[str, object] = prior.model_dump(mode="json", by_alias=True)
    for field_patch in patch.patches:
        segments = _parse_path(field_patch.field_path)
        # A patch may not *target* a framework-owned field: a bare scalar leaf (e.g. ...
        # source_of_record / ...framework_enriched) or a top-level index (material_discrepancies /
        # reproducibility) slips the shape-based value-scrub below, so reject it by path here
        # (ADR 0087). The denylist is generated from the inline FrameworkOwned markers.
        _reject_framework_owned_path(segments, field_patch.field_path)
        # The patch value is LLM-authored content for a flagged path; like a first-run extract it
        # must not author framework-owned provenance/ids *nested inside* a legitimately-targeted
        # content sub-tree (a whole-Provenance patch at a content field). Scrub the patch sub-tree
        # only — never the merged spec — so a prior iteration's legitimate enrichment survives
        # (ADR 0085).
        scrubbed = neutralize_patch_provenance(field_patch.new_value)
        _set_by_path(data, segments, scrubbed, path=field_patch.field_path)
    return AttackSpec.model_validate(data)


def _reject_framework_owned_path(segments: list[str | int], field_path: str) -> None:
    """Raise :class:`RefinementPathError` if ``segments`` target a framework-owned field (ADR 0087).

    A jury-``revise`` patch is LLM-authored; it may never author a field the framework owns. The
    value-scrub catches a whole-object forgery by shape, but a bare scalar leaf or a top-level
    index slips it — so reject by path. The denylist is **generated** from the inline
    ``FrameworkOwned`` markers (``framework_owned_path_buckets``): a marker on the AttackSpec root
    is matched as the leading segment; a marker on a nested model as the target leaf name.
    """
    root_names, leaf_names = framework_owned_path_buckets()
    head = segments[0]
    if isinstance(head, str) and head in root_names:
        raise RefinementPathError(
            f"field_path {field_path!r} targets the framework-owned top-level field {head!r}; "
            "an LLM patch may not author it (ADR 0087)"
        )
    leaf = segments[-1]
    if isinstance(leaf, str) and leaf in leaf_names:
        raise RefinementPathError(
            f"field_path {field_path!r} targets the framework-owned field {leaf!r}; "
            "an LLM patch may not author it (ADR 0087)"
        )


# --- path parsing + deep-set (deterministic, dependency-free) ---------------

#: One dotted segment: a field name followed by zero or more ``[index]`` groups.
_SEGMENT_RE = re.compile(r"^(?P<name>[^.\[\]]+)(?P<indices>(?:\[[^\[\]]+\])*)$")
_INDEX_RE = re.compile(r"\[([^\[\]]+)\]")


def _parse_path(path: str) -> list[str | int]:
    """Parse ``"chain.chain_steps[0].description"`` into ``["chain","chain_steps",0,"description"]``.

    Object field names become ``str`` segments; ``[i]`` becomes an ``int`` segment. A
    non-integer index (e.g. ``[step-1]``) or a malformed segment raises
    :class:`RefinementPathError` (the Phase-1 dotted + integer-index convention).
    """
    if not path.strip():
        raise RefinementPathError("empty field_path")
    segments: list[str | int] = []
    for part in path.split("."):
        match = _SEGMENT_RE.match(part)
        if match is None:
            raise RefinementPathError(f"malformed segment {part!r} in field_path {path!r}")
        segments.append(match.group("name"))
        for raw in _INDEX_RE.findall(match.group("indices")):
            if not raw.isdigit():
                raise RefinementPathError(
                    f"non-integer index [{raw}] in field_path {path!r}; Phase-1 patch paths use "
                    "dotted names with integer list indices (e.g. chain.chain_steps[0].description)"
                )
            segments.append(int(raw))
    return segments


def _set_by_path(
    root: dict[str, object], segments: list[str | int], value: JsonValue, *, path: str
) -> None:
    """Set ``value`` at ``segments`` in ``root``, requiring every segment to already resolve.

    The leaf key/index must already exist (we *replace*, never invent), so a patch to a
    field absent from the prior spec is rejected rather than silently created. Typing the
    cursor as ``dict[str, object] | list[object]`` lets each per-segment ``isinstance`` check
    narrow to a *typed* container (not ``Unknown``), so indexing stays statically sound.
    """
    # Descend to the container holding the leaf, then set the leaf on it. Read (traversal)
    # and write (leaf) are split into separate helpers so neither scope mixes a subscript
    # write and read — which trips pyright's narrowing of a ``dict | list`` container.
    cursor: dict[str, object] | list[object] = root
    for segment in segments[:-1]:
        child = _read_child(cursor, segment, path)
        if not _is_container(child):
            raise RefinementPathError(
                f"field_path {path!r} descends through a scalar at segment {segment!r}"
            )
        cursor = cast("dict[str, object] | list[object]", child)
    _set_leaf(cursor, segments[-1], value, path)


def _read_child(
    container: dict[str, object] | list[object], segment: str | int, path: str
) -> object:
    """Return the child at ``segment``; raise if the segment doesn't resolve in ``container``."""
    if isinstance(container, list):
        if not isinstance(segment, int):
            raise RefinementPathError(
                f"key {segment!r} in field_path {path!r} indexes a list; use an integer index"
            )
        if not 0 <= segment < len(container):
            raise RefinementPathError(
                f"index [{segment}] is out of range in field_path {path!r} (len {len(container)})"
            )
        return container[segment]
    if not isinstance(segment, str):
        raise RefinementPathError(
            f"index [{segment}] in field_path {path!r} indexes an object; expected a key"
        )
    if segment not in container:
        raise RefinementPathError(
            f"key {segment!r} does not resolve in field_path {path!r} "
            "(a patch may only replace an existing field, not invent one)"
        )
    return container[segment]


def _set_leaf(
    container: dict[str, object] | list[object], segment: str | int, value: JsonValue, path: str
) -> None:
    """Set ``value`` at ``segment`` on ``container``; the segment must already resolve."""
    if isinstance(container, list):
        if not isinstance(segment, int):
            raise RefinementPathError(
                f"key {segment!r} in field_path {path!r} indexes a list; use an integer index"
            )
        if not 0 <= segment < len(container):
            raise RefinementPathError(
                f"index [{segment}] is out of range in field_path {path!r} (len {len(container)})"
            )
        container[segment] = value
        return
    if not isinstance(segment, str):
        raise RefinementPathError(
            f"index [{segment}] in field_path {path!r} indexes an object; expected a key"
        )
    if segment not in container:
        raise RefinementPathError(
            f"key {segment!r} does not resolve in field_path {path!r} "
            "(a patch may only replace an existing field, not invent one)"
        )
    container[segment] = value


def _is_container(value: object) -> bool:
    """True when ``value`` is a JSON object or array (a dict or list)."""
    return isinstance(value, dict | list)


__all__ = [
    "FieldPatch",
    "RefinementPatch",
    "RefinementPathError",
    "apply_field_patch",
]
