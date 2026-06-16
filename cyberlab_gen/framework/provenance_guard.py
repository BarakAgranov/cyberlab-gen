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

from typing import Any, cast

from pydantic import JsonValue

from cyberlab_gen.schemas.attack_spec import AttackSpec

#: The keys that together identify a serialized ``Provenance`` mapping — it is the only model
#: carrying ``framework_enriched`` alongside ``source`` and ``citations`` (an ``ExtrasEntry``
#: has ``source``/``citations`` but no ``framework_enriched``, so it is left untouched).
_PROVENANCE_MARKERS = frozenset({"source", "citations", "framework_enriched"})


def _scrub_node(node: object) -> None:
    """Recursively reset framework-owned provenance + framework-set ids in ``node``.

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


def neutralize_framework_owned_provenance(spec: AttackSpec) -> AttackSpec:
    """Return a copy of ``spec`` with every framework-owned field the LLM may not author reset.

    Applied to a WHOLE Extractor output — first run, structural retry, grounding retry — at the
    orchestrator's extract seam, before validation / enrichment / grounding. The LLM (re)authored
    the entire spec on these paths, so the framework scrubs the entire spec. Targeted-patch
    refinement is handled at the merge seam instead (:func:`neutralize_patch_provenance`), so a
    prior iteration's legitimate enrichment is not wiped (ADR 0085).

    Resets, across the whole spec: ``framework_enriched`` + the API-override discrepancy record on
    every ``Provenance``; every ``CveReference.source_of_record`` to ``None``; the top-level
    ``material_discrepancies`` index to ``[]``; and the lab-level ``reproducibility`` block to
    ``None`` (framework-derived from the per-step tiers, never authored upfront). Idempotent.
    """
    data = spec.model_dump()
    data["material_discrepancies"] = []
    data["reproducibility"] = None
    _scrub_node(data)
    return type(spec).model_validate(data)


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
