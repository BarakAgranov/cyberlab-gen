"""Framework subpackage — deterministic orchestration code.

Houses control-flow, routing, retry, refinement-loop coordination, and
shared-state mutation. Per `docs/architecture.md §1.5`, the framework owns the
LLM-vs-framework split: agents produce content; the framework decides routing,
retry budgets, stopping, and shipping. Empty in Phase 0; populated as the
pipeline stages land in Phase 1+.

Phase 1 Task 3 adds the Ingestion stage (`pipeline.md §3.2.1`): fetch a blog
URL, normalize HTML to heading-preserving text, hash it, cache it, and record
an `IngestionResult`. Re-exported here so cross-subpackage callers (the CLI's
`extract` verb, the orchestrator) import through the package surface.
"""

from cyberlab_gen.framework.enrichment import (
    EnrichmentConfig,
    EnrichmentResult,
    HttpxNvdClient,
    LookupPriority,
    NvdClient,
    NvdCveData,
    SkippedLookup,
    enrich,
)
from cyberlab_gen.framework.ingestion import (
    DEFAULT_TIMEOUT_SECONDS,
    IngestionConfig,
    compute_content_hash,
    ingest,
    normalize_html,
    read_cached,
    read_cached_text,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "EnrichmentConfig",
    "EnrichmentResult",
    "HttpxNvdClient",
    "IngestionConfig",
    "LookupPriority",
    "NvdClient",
    "NvdCveData",
    "SkippedLookup",
    "compute_content_hash",
    "enrich",
    "ingest",
    "normalize_html",
    "read_cached",
    "read_cached_text",
]
