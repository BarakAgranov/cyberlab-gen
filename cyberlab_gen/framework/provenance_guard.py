"""Neutralize framework-owned fields the LLM must not author (ADR 0082, narrowed by ADR 0085).

The pre-Planner enrichment pass (``pipeline.md §3.2.4``) is the SOLE legitimate writer of the
framework-owned provenance fields — ``framework_enriched`` and the API-override discrepancy
record (``schema.md §4.9`` framework-only authorship) — of the top-level ``material_discrepancies``
list (``architecture.md §1.5``: an LLM never modifies shared state outside its designated output),
and of ``CveReference.source_of_record`` (set only after a successful enrichment lookup). The
lab-level ``reproducibility`` block is framework-DERIVED from the per-step tiers
(``architecture.md §0.7``), never authored upfront. Enrichment/derivation run *after* extraction,
so at the post-extraction seam every such field must be False/None/empty.

Any True/set value present in an Extractor output was therefore LLM-authored, and is dangerous:
a self-stamped ``framework_enriched`` is doubly evaded (enrichment skips an already-enriched field
as a no-op (``enrichment.py``), and the grounding search-before-claim check EXEMPTS
``framework_enriched`` fields (``grounding_validator.py``)), so the mechanical hallucination check
is bypassed — a ``§1.6`` mechanical-safety hole; a forged ``source_of_record`` falsely attributes a
CVE value to an authoritative source the lab never queried.

**Two seams (ADR 0085).** A *first run* / structural retry / grounding retry (re)authors the WHOLE
spec, so :func:`neutralize_framework_owned_provenance` scrubs the whole spec. A *targeted-patch
refinement* (jury ``revise``) authors only the patch's ``new_value`` sub-trees, so
:func:`neutralize_patch_provenance` scrubs only those — at the merge seam, before
``apply_field_patch`` deep-sets them — leaving a prior iteration's legitimate enrichment and the
top-level indices intact. The earlier blanket reset of the *merged* refine output (ADR 0082) wiped
a prior-iteration blog-vs-API discrepancy that re-enrichment could no longer re-detect (the field
was already ``external_api``), silently dropping it.
"""

from collections.abc import Iterator
from typing import Any, cast, get_args, get_origin

from pydantic import BaseModel, JsonValue

from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.framework_owned import framework_owned_fields

#: The keys that together identify a serialized ``Provenance`` mapping — it is the only model
#: carrying ``framework_enriched`` alongside ``source`` and ``citations`` (an ``ExtrasEntry``
#: has ``source``/``citations`` but no ``framework_enriched``, so it is left untouched).
_PROVENANCE_MARKERS = frozenset({"source", "citations", "framework_enriched"})


def _scrub_node(node: object) -> None:
    """Recursively reset framework-owned provenance + framework-set ids in ``node`` by SHAPE.

    Used only by :func:`neutralize_patch_provenance` (the pre-validation patch-value path, where
    a node's type is not yet known so it must be recognized by shape). The whole-spec reset is
    marker-driven instead (:func:`_reset_owned_instance`, ADR 0087); this shape recognition is the
    one remaining re-derivation, and is exactly the residual the deferred path→type resolver
    subsumes (ADR 0087). Two recognizers:

    - On every serialized ``Provenance`` mapping (the only model carrying ``framework_enriched``
      alongside ``source``/``citations``): reset ``framework_enriched`` and the three-field
      API-override discrepancy record together, so the validator's required-when invariant stays
      satisfied.
    - On every serialized ``CveReference`` (identified by its ``cve_id`` key): null
      ``source_of_record`` — the framework's enrichment pass sets it only after a successful
      lookup (``enrichment.py``); an Extractor-authored value would otherwise survive on every
      skipped lookup as a forged authoritative-source claim (ADR 0085). ``MaterialDiscrepancy``
      also carries ``source_of_record`` but has no ``cve_id`` and is framework-authored, so the
      ``cve_id`` discriminator leaves it untouched.
    """
    if isinstance(node, dict):
        mapping = cast("dict[str, Any]", node)
        if mapping.keys() >= _PROVENANCE_MARKERS:
            mapping["framework_enriched"] = False
            mapping["discrepancy_with_blog"] = False
            mapping["overridden_blog_value"] = None
            mapping["discrepancy_classification"] = None
        if "cve_id" in mapping:
            mapping["source_of_record"] = None
        for value in mapping.values():
            _scrub_node(value)
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            _scrub_node(item)


def _reset_owned_value(value: object) -> object:
    """Recurse into nested models / lists / dicts, resetting framework-owned fields (ADR 0087)."""
    if isinstance(value, BaseModel):
        return _reset_owned_instance(value)
    if isinstance(value, list):
        return [_reset_owned_value(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        return {k: _reset_owned_value(v) for k, v in cast("dict[Any, object]", value).items()}
    return value


def _reset_owned_instance[M: BaseModel](obj: M) -> M:
    """Return a copy of ``obj`` with its framework-owned fields reset to their declared defaults.

    Walks the model *instance*, so each node's exact class is known (``type(obj)``) and ownership
    is read from that class's inline ``FrameworkOwned`` markers (ADR 0087) — no shape heuristics,
    and no annotation/union resolution to get wrong (the instance carries its own type). All owned
    fields on a node reset together, so the discrepancy-record coupling stays satisfied.
    """
    cls = type(obj)
    owned = framework_owned_fields(cls)
    updates: dict[str, object] = {}
    for name, info in cls.model_fields.items():
        if name in owned:
            updates[name] = info.get_default(call_default_factory=True)
            continue
        value = getattr(obj, name)
        new_value = _reset_owned_value(value)
        if new_value is not value:
            updates[name] = new_value
    return obj.model_copy(update=updates) if updates else obj


def neutralize_framework_owned_provenance(spec: AttackSpec) -> AttackSpec:
    """Return a copy of ``spec`` with every framework-owned field the LLM may not author reset.

    Applied to a WHOLE Extractor output — first run, structural retry, grounding retry — at the
    orchestrator's extract seam, before validation / enrichment / grounding. The LLM (re)authored
    the entire spec on these paths, so the framework resets the entire spec. Targeted-patch
    refinement is handled at the merge seam instead (:func:`neutralize_patch_provenance`), so a
    prior iteration's legitimate enrichment is not wiped (ADR 0085).

    Marker-driven (ADR 0087): a walk over the model tree resets every field declared
    ``FrameworkOwned`` to its default — ``framework_enriched`` + the API-override discrepancy
    record on every ``Provenance``; every ``CveReference.source_of_record``; the top-level
    ``material_discrepancies`` index; the lab-level ``reproducibility`` block. The set comes from
    the inline markers, not the shape heuristics of :func:`_scrub_node` (which remains only for the
    pre-validation patch-value path, where types are not yet known). Idempotent. Re-validated so
    the seam hands a self-consistent spec onward.
    """
    return type(spec).model_validate(_reset_owned_instance(spec).model_dump())


def neutralize_patch_provenance(new_value: JsonValue) -> JsonValue:
    """Scrub framework-owned provenance/ids from one refinement patch's ``new_value`` before merge.

    A jury-``revise`` patch is LLM-authored content for the flagged paths; like a first-run extract
    it must not author ``framework_enriched`` / the discrepancy record / a
    ``CveReference.source_of_record``. Unlike the first run the framework scrubs ONLY this patch
    sub-tree — never the merged spec — so a prior iteration's legitimately-enriched fields and the
    top-level ``material_discrepancies`` / ``reproducibility`` rollups survive the refinement
    (ADR 0085, narrowing ADR 0082's over-broad merged-spec reset). A field patch addresses a
    content path, never a top-level index, so those indices are deliberately not touched here.
    Mutates ``new_value`` in place (a transient patch value) and returns it.
    """
    _scrub_node(new_value)
    return new_value


# --- marker-aware refinement path→type resolver (ADR 0087) -----------------
#
# The refinement patch-path check rejects any ``field_path`` that *targets* a framework-owned
# field. Phase 1 generated a flat positional denylist (root-marked field → top-level segment;
# nested-marked field → leaf name) from the inline ``FrameworkOwned`` markers. That was correct for
# ``AttackSpec`` — its one ambiguous name, ``reproducibility``, is owned only at the *top level*
# (``AttackSpec.reproducibility``) while the per-step ``chain.chain_steps[*].reproducibility`` is
# authored content — because position separated them. It **expires at LabManifest refinement**
# (ADR 0087, ``dev/phase-2-seams.md`` §2): there ``CoreBlock.reproducibility`` (owned) and
# ``phases[*].steps[*].reproducibility`` (content) are BOTH nested, so position cannot tell them
# apart. So the patch path now walks the *schema* from the artifact root to the exact
# ``(model, field)`` the path names and reads that field's marker — no positional heuristic, correct
# for both artifacts. The patch-value shape scrub (:func:`_scrub_node`) remains as defence-in-depth.


def _models_in_annotation(annotation: object) -> Iterator[type[BaseModel]]:
    """Yield every ``BaseModel`` subclass embedded in a field annotation.

    Unwraps ``T | None``, ``list[T]``, ``dict[K, V]``, and the ``Provenance[T]`` generic (a
    parametrized generic is itself a ``BaseModel`` subclass, so it is yielded directly).
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in get_args(annotation):
        yield from _models_in_annotation(arg)


def _list_element_annotation(annotation: object) -> object:
    """The element annotation of a ``list[T]`` (unwrapping a surrounding ``list[T] | None``).

    Returns the bare ``object`` sentinel when ``annotation`` is not a list, so the caller's model
    lookup yields nothing and the path is treated as un-owned (the deep-set then raises the precise
    :class:`RefinementPathError` for the malformed shape).
    """
    for candidate in (annotation, *get_args(annotation)):
        if get_origin(candidate) is list:
            args = get_args(candidate)
            if args:
                return args[0]
    return object


def resolve_framework_owned(root: type[BaseModel], segments: list[str | int]) -> bool:
    """True iff ``segments`` *target or descend into* a ``FrameworkOwned`` field of ``root``'s schema.

    Walks the field *annotations* (not a data instance), unwrapping ``T | None`` / ``list[T]`` /
    the ``Provenance[T]`` generic at each step, reading each field's inline marker (ADR 0087). A
    ``[i]`` index segment descends a ``list[T]`` to ``T``.

    **Ancestor-aware (ADR 0091 follow-up).** Ownership is checked at *every* string segment, not only
    the terminal one: if any ancestor segment names an owned field, the whole sub-tree is owned and
    the path is rejected. The earlier terminal-only walk regressed the deleted flat positional
    check's leading-segment rule — a patch that *descended into* an owned container slipped the guard
    (a trailing index on the owned ``material_discrepancies`` list; a sub-field of the owned
    ``reproducibility`` / ``core.reproducibility`` block), re-opening the ``§1.6`` forge hole an LLM
    ``revise`` patch could drive. Because ownership is read off each *exact* model, the authored
    ``phases[*].steps[*].reproducibility`` (``StepBlock`` unmarked) stays a legitimate target while
    the owned ``core.reproducibility`` (``CoreBlock`` marked) and its sub-fields are rejected.

    Returns ``False`` for a path that does not resolve in the schema (or resolves ambiguously through
    a multi-model union mid-walk) — the deep-set then raises the precise :class:`RefinementPathError`;
    this guard's sole job is to reject an owned *target*.
    """
    current: object = root
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        if isinstance(segment, int):
            current = _list_element_annotation(current)
            continue
        carriers = [m for m in _models_in_annotation(current) if segment in m.model_fields]
        # Owned at ANY position — terminal or ancestor — means the path targets or descends into a
        # framework-owned field, which an LLM patch may not author.
        if any(segment in framework_owned_fields(model) for model in carriers):
            return True
        # Terminal & not owned → not an owned target. A mid-walk multi-model union (len != 1) is
        # treated as un-owned — this errs toward ALLOW, sound only while no schema field is a
        # divergent-ownership model-union (an `A | B` hiding an owned field below the union point);
        # none exists today. If one ever lands, pin it with a test (the deep-set still raises
        # RefinementPathError for a path that truly does not resolve).
        if i == last or len(carriers) != 1:
            return False
        current = carriers[0].model_fields[segment].annotation
    return False
