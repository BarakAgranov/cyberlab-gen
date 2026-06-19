"""Shared adapter support: citations, discrepancy recording, trigger resolution.

Neutral helpers the per-source adapters reuse (depends only on ``schemas`` +
``types``). Keeping them here keeps each adapter focused on its source's wire
format. Relocated/added with the data-driven seam (ADR 0101).

Trigger resolution (``pipeline.md §3.2.4`` "runs ``enrichment_triggers`` from the
registry") is deliberately **minimal**: it resolves the trigger-field dialects
that name real AttackSpec collections in this schema version — the CVE list
(``external_references.cves[*]``), the chain-step MITRE ids, and ``facets``
membership for a ``target:<cloud>`` predicate. A declared trigger whose field has
no home in the current schema (OSV's ``chain.chain_steps[*].targets.packages[*]``,
GitHub's ``...targets.repos[*]``) does **not** silently match — the owning adapter
records an honest skip naming the gap. Generalising the predicate mini-language is
deferred with the schema fields those triggers need (ADR 0101 §"owned deferrals").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from cyberlab_gen.external_data_sources.types import SkippedLookup
from cyberlab_gen.schemas.attack_spec import MaterialDiscrepancy
from cyberlab_gen.schemas.enums import CitationKind
from cyberlab_gen.schemas.provenance import CitationBlock

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.types import EnrichmentResult
    from cyberlab_gen.schemas.attack_spec import AttackSpec, CveReference
    from cyberlab_gen.schemas.registries import EnrichmentTrigger, ExternalDataSourceEntry

#: Reason recorded when an external source rate-limits a framework lookup
#: (``pipeline.md §3.2.4``). The exact wording the brief mandates.
RATE_LIMITED_REASON = "external API rate-limited at enrichment time"
#: Reason recorded when an external source is otherwise unreachable. Never fatal
#: (ADR 0042); the lab still generates and the gap shows up in provenance.
UNAVAILABLE_REASON = "external source {source_id} unavailable at enrichment time"
#: Reason recorded for a source registered in the registry but with no adapter /
#: no injected client this run.
NOT_INTEGRATED_REASON = "source {source_id} not integrated this run (no live client)"


# --- citations ---------------------------------------------------------------


def blog_citation(locator: str) -> CitationBlock:
    """A citation back to where the blog mentioned the id.

    The blog-passage locator is the id the framework found in the AttackSpec; the
    richer per-step excerpt lives on the chain step. Both citations are preserved
    on every rewrite (``schema.md §4.9``).
    """
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference=locator)


def api_citation(source_id: str, locator: str) -> CitationBlock:
    """A citation back to the external API/catalog response."""
    return CitationBlock(
        kind=CitationKind.EXTERNAL_API_RESPONSE,
        reference=f"{source_id}:{locator}",
    )


# --- discrepancy recording ---------------------------------------------------


def record_discrepancy(
    spec: AttackSpec,
    result: EnrichmentResult,
    *,
    path: str,
    blog_value: str,
    authoritative_value: str,
    source_id: str,
    classification: str,
    summary: str,
) -> None:
    """Record a (material or non-material) discrepancy.

    Always recorded for audit. Material ones additionally append a
    ``MaterialDiscrepancy`` to the spec's top-level index list (framework-owned,
    ADR 0017/0087).
    """
    if classification == "material":
        discrepancy = MaterialDiscrepancy(
            field_path=path,
            summary=summary,
            blog_value=blog_value,
            authoritative_value=authoritative_value,
            source_of_record=source_id,
        )
        result.material_discrepancies.append(discrepancy)
        spec.material_discrepancies.append(discrepancy)
    else:
        result.non_material_field_paths.append(path)


# --- trigger resolution ------------------------------------------------------

_CVE_LIST_RE = re.compile(r"external_references\.cve")
_TECHNIQUE_RE = re.compile(r"techniques\.mitre")
_FACET_PREDICATE_RE = re.compile(r"facets\[\?value=['\"](?P<value>[^'\"]+)['\"]\]")


def cve_refs(spec: AttackSpec) -> list[CveReference]:
    """The CVE references the spec carries (the resolvable CVE-list trigger target)."""
    refs = spec.external_references
    return list(refs.cves) if refs is not None else []


def triggers_cve_list(entry: ExternalDataSourceEntry) -> bool:
    """True when any of the entry's triggers fires on the AttackSpec CVE list.

    Matches both the real schema path (``external_references.cves[*]``) and the
    doc's historical ``external_references.cve_references[*]`` spelling.
    """
    return any(_CVE_LIST_RE.search(str(t.field)) for t in entry.enrichment_triggers)


def triggers_techniques(entry: ExternalDataSourceEntry) -> bool:
    """True when any of the entry's triggers fires on the chain-step MITRE ids."""
    return any(_TECHNIQUE_RE.search(str(t.field)) for t in entry.enrichment_triggers)


def triggered_facet(entry: ExternalDataSourceEntry) -> str | None:
    """Return the ``target:<cloud>`` value a ``facets[?value='…']`` trigger names, if any."""
    for trigger in entry.enrichment_triggers:
        match = _FACET_PREDICATE_RE.search(str(trigger.field))
        if match:
            return match.group("value")
    return None


def spec_has_facet(spec: AttackSpec, value: str) -> bool:
    """True when the spec declares the given ``category:value`` facet."""
    return any(str(f) == value for f in spec.facets)


def unresolved_triggers(
    entry: ExternalDataSourceEntry,
) -> list[EnrichmentTrigger]:
    """The entry's triggers whose field names no resolvable AttackSpec collection.

    Used by an adapter (OSV) whose declared trigger field has no home in the
    current schema, so it can record an honest skip per gap rather than silently
    enriching nothing (ADR 0101).
    """
    out: list[EnrichmentTrigger] = []
    for trigger in entry.enrichment_triggers:
        field = str(trigger.field)
        resolvable = (
            _CVE_LIST_RE.search(field)
            or _TECHNIQUE_RE.search(field)
            or _FACET_PREDICATE_RE.search(field)
        )
        if not resolvable:
            out.append(trigger)
    return out


def record_skip(result: EnrichmentResult, *, field_path: str, source_id: str, reason: str) -> None:
    """Append a ``SkippedLookup`` to the result (the honest "didn't / couldn't" record)."""
    result.skipped.append(SkippedLookup(field_path=field_path, source_id=source_id, reason=reason))


__all__ = [
    "api_citation",
    "blog_citation",
    "cve_refs",
    "record_discrepancy",
    "record_skip",
    "spec_has_facet",
    "triggered_facet",
    "triggers_cve_list",
    "triggers_techniques",
    "unresolved_triggers",
]
