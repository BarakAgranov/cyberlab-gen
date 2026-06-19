"""Pre-Planner enrichment: the deterministic, **data-driven** framework pass (``pipeline.md §3.2.4``).

Framework code — **never an agent** (CLAUDE.md hard rules; ``schema.md §4.9``
"framework-only authorship"). This module is the *driver*: it walks the
``external_data_sources`` registry entries, resolves a per-source
``SourceAdapter`` for each (``external_data_sources.registry.resolve_adapter`` —
**no more hardcoded ``_NVD_SOURCE_ID`` dispatch**), and runs them in budget
priority order. The per-source enrichment logic lives behind the seam, under
``cyberlab_gen/external_data_sources/<id>/`` (ADR 0101 / ``dev/phase-2-seams.md``
③.2; the ADR 0077 work-stream).

What each adapter does (``schema.md §4.9``): set each enriched field's provenance
to ``source=external_api`` with both citations, marking it ``framework_enriched``;
record blog-vs-API discrepancies, classifying material vs non-material per the
source entry's ``discrepancy_materiality_rules``. NVD's CVSS/severity are the one
*corroboration-capable* pair (the blog can claim them) and live as typed
provenance on ``CveReference``; the secondary sources (KEV/EPSS/MSRC/bulletins)
are additive signals with no blog counterpart and land as typed records in the
``EnrichmentResult`` audit channel (ADR 0101 §"schema-home gap").

Budget (``pipeline.md §3.2.4``): a per-run cap (default 100) on framework-issued
external (non-local) calls, spent in priority order CVEs > MITRE > GitHub >
bulletins > other. Lookups skipped because the budget is exhausted, a source is
unavailable/rate-limited, or a source has no registered adapter / no live client
are recorded as ``SkippedLookup`` records naming the gap honestly. MITRE catalog
lookups are local and do not consume the external-call budget. **Source
unavailability is never fatal** (ADR 0042).

Authorship discipline (``architecture.md §1.5/§1.6``): mechanical, deterministic
framework code. No LLM decides what to enrich, whether a discrepancy is material,
or whether to stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

# Re-exported for back-compat: these moved to the neutral ``external_data_sources``
# package (the ``NvdClient`` relocation, ADR 0077 / seams ④). Importers that still
# do ``from cyberlab_gen.framework.enrichment import NvdClient`` keep working.
from cyberlab_gen.external_data_sources import (
    EnrichmentContext,
    EnrichmentResult,
    HttpxNvdClient,
    LookupPriority,
    NvdClient,
    NvdCveData,
    SkippedLookup,
    SourceClients,
    registered_source_ids,
    resolve_adapter,
)
from cyberlab_gen.external_data_sources import support as _support
from cyberlab_gen.registries.loader import load_mitre_techniques
from cyberlab_gen.registries.merge import load_merged_registries

if TYPE_CHECKING:
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.registries import (
        ExternalDataSourceEntry,
        MitreTechniqueCatalog,
    )

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = 100


@dataclass
class EnrichmentConfig:
    """Tunable knobs for the enrichment pass (``pipeline.md §3.2.4``).

    Plain ``@dataclass`` (no ``slots=True``): the injected fields carry
    forward-referenced types (``MergedRegistries``/``MitreTechniqueCatalog`` are
    ``TYPE_CHECKING``-only imports), and a slotted dataclass builds a member
    descriptor per field whose access path differs subtly from attribute access
    here — a non-slotted dataclass keeps plain instance attributes.
    """

    budget: int = _DEFAULT_BUDGET
    """Per-run cap on framework-issued external (non-local) calls."""

    nvd_client: NvdClient | None = None
    """Back-compat convenience: the NVD client. Mapped into ``clients.nvd``. ``None``
    disables live CVE enrichment (every CVE lookup is then skipped not-integrated)."""

    clients: SourceClients | None = None
    """The full injectable per-source client bundle. ``None`` → no live clients
    (the hermetic default); a partial bundle is fine — each source with a ``None``
    slot is skipped, never fatal."""

    mitre_catalog: MitreTechniqueCatalog | None = None
    """Injected MITRE catalog; defaults to the bundled one when ``None``."""

    registries: MergedRegistries | None = None
    """Injected merged registries (for the ``external_data_sources`` entries +
    their ``discrepancy_materiality_rules``); defaults to the bundled+overlay
    merge when ``None``."""

    integrated_sources: frozenset[str] = field(default_factory=registered_source_ids)
    """Source ids whose adapters are allowed to run. Defaults to every registered
    adapter; a test may pass a subset to force a source off. A registry entry with
    no registered adapter (or excluded here) gets an honest stub-skip."""


def _merge_clients(cfg: EnrichmentConfig) -> SourceClients:
    """Fold the back-compat ``nvd_client`` into the full ``SourceClients`` bundle."""
    base = cfg.clients if cfg.clients is not None else SourceClients()
    return SourceClients(
        nvd=cfg.nvd_client if cfg.nvd_client is not None else base.nvd,
        kev=base.kev,
        epss=base.epss,
        msrc=base.msrc,
        bulletins=base.bulletins,
    )


def _record_stub_skips(entry: ExternalDataSourceEntry, result: EnrichmentResult) -> None:
    """Record honest skips for a registry entry with no resolvable adapter.

    For a source that declares ``enrichment_triggers`` but has no registered
    adapter (or was excluded via ``integrated_sources``), surface the absence once
    per declared trigger so the gap is honest in the run report.
    """
    for trigger in entry.enrichment_triggers:
        _support.record_skip(
            result,
            field_path=str(trigger.field),
            source_id=entry.id,
            reason=_support.NOT_INTEGRATED_REASON.format(source_id=entry.id),
        )


def enrich(spec: AttackSpec, config: EnrichmentConfig | None = None) -> EnrichmentResult:
    """Run the data-driven pre-Planner enrichment pass over ``spec`` (``pipeline.md §3.2.4``).

    Iterates the ``external_data_sources`` registry, resolves an adapter per entry,
    and runs each in budget priority order. Mutates ``spec`` in place (rewrites
    NVD-enriched CVE provenance, appends material discrepancies) and returns the
    full ``EnrichmentResult`` account the run report consumes. Framework code only.
    """
    cfg = config if config is not None else EnrichmentConfig()
    result = EnrichmentResult()
    budget = [cfg.budget]
    registries = cfg.registries if cfg.registries is not None else load_merged_registries()
    mitre_catalog = cfg.mitre_catalog if cfg.mitre_catalog is not None else load_mitre_techniques()
    ctx = EnrichmentContext(
        spec=spec,
        result=result,
        budget=budget,
        clients=_merge_clients(cfg),
        mitre_catalog=mitre_catalog,
    )

    entries = list(registries.external_data_sources.entries)
    resolved = [(idx, entry, resolve_adapter(entry.id)) for idx, entry in enumerate(entries)]
    # Run in budget priority order (CVEs > MITRE > GitHub > bulletins > other),
    # stable on registry order so NVD runs before the secondary CVE sources.
    resolved.sort(
        key=lambda item: (
            int(item[2].priority) if item[2] is not None else int(LookupPriority.OTHER),
            item[0],
        )
    )
    for _idx, entry, adapter in resolved:
        if adapter is None or entry.id not in cfg.integrated_sources:
            _record_stub_skips(entry, result)
            continue
        adapter.enrich(ctx, entry)

    logger.info(
        "enrichment: %d calls, %d enriched, %d material, %d non-material, %d skipped; "
        "records kev=%d epss=%d msrc=%d bulletins=%d",
        result.calls_made,
        len(result.enriched_field_paths),
        len(result.material_discrepancies),
        len(result.non_material_field_paths),
        len(result.skipped),
        len(result.kev_records),
        len(result.epss_records),
        len(result.msrc_records),
        len(result.bulletin_records),
    )
    return result


__all__ = [
    "EnrichmentConfig",
    "EnrichmentResult",
    "HttpxNvdClient",
    "LookupPriority",
    "NvdClient",
    "NvdCveData",
    "SkippedLookup",
    "enrich",
]
