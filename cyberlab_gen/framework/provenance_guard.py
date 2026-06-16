"""Neutralize framework-only provenance the LLM must not author (ADR 0082).

The pre-Planner enrichment pass (``pipeline.md §3.2.4``) is the SOLE legitimate writer of the
framework-owned provenance fields — ``framework_enriched`` and the API-override discrepancy
record (``schema.md §4.9`` framework-only authorship) — and of the top-level
``material_discrepancies`` list (``architecture.md §1.5``: an LLM never modifies shared state
outside its designated output). Enrichment runs *after* extraction, so at the post-extraction
seam every such field must be False/None/empty.

Any True/set value present in an Extractor output was therefore LLM-authored, and is doubly
dangerous: enrichment skips an already-``framework_enriched`` field as a no-op
(``enrichment.py``), and the grounding stack's search-before-claim check EXEMPTS
``framework_enriched`` fields (``grounding_validator.py``). So an LLM (a hallucination, or a
prompt-injection from blog content) that self-stamps the flag would bypass the mechanical
hallucination check entirely — a ``§1.6`` mechanical-safety hole. The framework resets these
fields on every Extractor output before it processes the spec.
"""

from typing import Any, cast

from cyberlab_gen.schemas.attack_spec import AttackSpec

#: The keys that together identify a serialized ``Provenance`` mapping — it is the only model
#: carrying ``framework_enriched`` alongside ``source`` and ``citations`` (an ``ExtrasEntry``
#: has ``source``/``citations`` but no ``framework_enriched``, so it is left untouched).
_PROVENANCE_MARKERS = frozenset({"source", "citations", "framework_enriched"})


def _scrub_provenance(node: object) -> None:
    """Recursively reset the framework-only fields on every ``Provenance`` mapping in ``node``."""
    if isinstance(node, dict):
        mapping = cast("dict[str, Any]", node)
        if mapping.keys() >= _PROVENANCE_MARKERS:
            mapping["framework_enriched"] = False
            # The discrepancy record is written only by enrichment when an API contradicts the
            # blog (``schema.md §4.9``); reset all three together so the validator's
            # required-when invariant stays satisfied.
            mapping["discrepancy_with_blog"] = False
            mapping["overridden_blog_value"] = None
            mapping["discrepancy_classification"] = None
        for value in mapping.values():
            _scrub_provenance(value)
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            _scrub_provenance(item)


def neutralize_framework_owned_provenance(spec: AttackSpec) -> AttackSpec:
    """Return a copy of ``spec`` with every framework-only provenance field reset.

    Resets ``framework_enriched`` and the API-override discrepancy record on every
    ``Provenance`` and clears the top-level ``material_discrepancies`` list — the fields only
    the framework's enrichment pass may author. Applied to every Extractor output (first run,
    structural retry, refinement patch) at the orchestrator's extract seam, before
    validation / enrichment / grounding. Idempotent.
    """
    data = spec.model_dump()
    data["material_discrepancies"] = []
    _scrub_provenance(data)
    return type(spec).model_validate(data)
