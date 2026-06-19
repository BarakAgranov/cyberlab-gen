"""External-data-source adapters behind the data-driven enrichment seam.

The neutral home (no ``framework`` dependency) for the pre-Planner enrichment
machinery (``pipeline.md §3.2.4``): the client ports, the typed records each
source produces, the per-source adapters, and the adapter registration table the
``framework.enrichment`` driver iterates. This realises the
``dev/phase-2-seams.md`` ③.2 (data-driven enrichment) + ④ (``NvdClient``
relocation) seams and the ADR 0077 work-stream — landed in ADR 0101.

External consumers (agents, validators, the orchestrator) import the client
ports and adapters through this package surface; ``framework.enrichment``
re-exports the result/config types for back-compat.
"""

from cyberlab_gen.external_data_sources.bulletins import (
    BulletinAdapter,
    HttpxBulletinClient,
    parse_rss_feed,
)
from cyberlab_gen.external_data_sources.epss import (
    EpssAdapter,
    HttpxEpssClient,
    parse_epss_response,
)
from cyberlab_gen.external_data_sources.kev import (
    HttpxKevClient,
    KevAdapter,
    parse_kev_catalog,
)
from cyberlab_gen.external_data_sources.mitre import MitreAdapter
from cyberlab_gen.external_data_sources.msrc import (
    HttpxMsrcClient,
    MsrcAdapter,
    parse_msrc_cvrf,
)
from cyberlab_gen.external_data_sources.nvd import (
    HttpxNvdClient,
    NvdAdapter,
    parse_nvd_response,
)
from cyberlab_gen.external_data_sources.osv import OsvAdapter
from cyberlab_gen.external_data_sources.ports import (
    BulletinClient,
    EnrichmentContext,
    EpssClient,
    KevClient,
    MsrcClient,
    NvdClient,
    SourceAdapter,
    SourceClients,
)
from cyberlab_gen.external_data_sources.registry import (
    registered_source_ids,
    resolve_adapter,
)
from cyberlab_gen.external_data_sources.types import (
    BulletinRecord,
    CveResolution,
    EnrichmentResult,
    EpssRecord,
    KevRecord,
    LookupPriority,
    MsrcRecord,
    MsrcRemediation,
    NvdCveData,
    SkippedLookup,
)

__all__ = [
    "BulletinAdapter",
    "BulletinClient",
    "BulletinRecord",
    "CveResolution",
    "EnrichmentContext",
    "EnrichmentResult",
    "EpssAdapter",
    "EpssClient",
    "EpssRecord",
    "HttpxBulletinClient",
    "HttpxEpssClient",
    "HttpxKevClient",
    "HttpxMsrcClient",
    "HttpxNvdClient",
    "KevAdapter",
    "KevClient",
    "KevRecord",
    "LookupPriority",
    "MitreAdapter",
    "MsrcAdapter",
    "MsrcClient",
    "MsrcRecord",
    "MsrcRemediation",
    "NvdAdapter",
    "NvdClient",
    "NvdCveData",
    "OsvAdapter",
    "SkippedLookup",
    "SourceAdapter",
    "SourceClients",
    "parse_epss_response",
    "parse_kev_catalog",
    "parse_msrc_cvrf",
    "parse_nvd_response",
    "parse_rss_feed",
    "registered_source_ids",
    "resolve_adapter",
]
