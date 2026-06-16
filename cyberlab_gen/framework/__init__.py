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
from cyberlab_gen.framework.orchestrator import (
    DEFAULT_REFINEMENT_CAP,
    DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    FeedbackKind,
    JuryRejectionError,
    PipelineOutcome,
    PipelineState,
    PipelineStatus,
    RefinementFeedback,
    build_pipeline,
    reject_interactive_when_headless,
    run_pipeline,
)
from cyberlab_gen.framework.reproducibility import (
    classify_lab_level,
    derive_lab_reproducibility,
)

__all__ = [
    "DEFAULT_REFINEMENT_CAP",
    "DEFAULT_STRUCTURAL_RETRY_ATTEMPTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EnrichmentConfig",
    "EnrichmentResult",
    "FeedbackKind",
    "HttpxNvdClient",
    "IngestionConfig",
    "JuryRejectionError",
    "LookupPriority",
    "NvdClient",
    "NvdCveData",
    "PipelineOutcome",
    "PipelineState",
    "PipelineStatus",
    "RefinementFeedback",
    "SkippedLookup",
    "build_pipeline",
    "classify_lab_level",
    "compute_content_hash",
    "derive_lab_reproducibility",
    "enrich",
    "ingest",
    "normalize_html",
    "read_cached",
    "read_cached_text",
    "reject_interactive_when_headless",
    "run_pipeline",
]
