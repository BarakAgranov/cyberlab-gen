"""Providers subpackage — LLM provider abstraction.

Houses the `Provider` ABC, capability hints, cost ledger, retry strategy, the
mock provider for tests, and per-vendor adapters. Architectural source:
`docs/provider-interface.md` and `docs/pipeline.md §3.5`. Phase 0 builds the
abstraction and scaffolds the Anthropic adapter (Tasks 5a + 5b); OpenAI lands
when its adapter is actually written (Phase 1+).

Provider errors (``ProviderError`` + 5 subtypes) live in
``cyberlab_gen.errors`` per ADR 0009's single-hierarchy rule. Import them
from there; this module deliberately does not re-export them so the
single-hierarchy invariant is visible at every import site.
"""

from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    Provider,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolExecutor,
    ToolResult,
)
from cyberlab_gen.providers.cost_ledger import (
    CallOutcome,
    CostLedger,
    CostLedgerEntry,
    CostReportBlock,
    ModelPricing,
    PricingTable,
    compute_cost,
    load_pricing_table,
)
from cyberlab_gen.providers.mock_provider import MockProvider, UnmatchedMockCall
from cyberlab_gen.providers.ranking import (
    ModelRankings,
    ProviderRegistry,
    RankingEntry,
    build_provider_registry,
    is_provider_configured,
    load_model_rankings,
)
from cyberlab_gen.providers.retries import (
    MALFORMED_OUTPUT_RETRIES,
    TRANSIENT_RETRIES,
    RetryStrategy,
)

__all__ = [
    "MALFORMED_OUTPUT_RETRIES",
    "TRANSIENT_RETRIES",
    "AgentLabel",
    "AnthropicProvider",
    "CallOutcome",
    "CapabilityHint",
    "CostLedger",
    "CostLedgerEntry",
    "CostReportBlock",
    "Message",
    "MessageRole",
    "MockProvider",
    "ModelPricing",
    "ModelRankings",
    "PricingTable",
    "Provider",
    "ProviderRegistry",
    "ProviderResponse",
    "RankingEntry",
    "RetryStrategy",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "ToolExecutor",
    "ToolResult",
    "UnmatchedMockCall",
    "build_provider_registry",
    "compute_cost",
    "is_provider_configured",
    "load_model_rankings",
    "load_pricing_table",
]
