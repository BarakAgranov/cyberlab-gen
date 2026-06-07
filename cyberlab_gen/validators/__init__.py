"""Validators subpackage — the mechanical validation layers.

Architectural source: ``validation.md`` (the four active v1 layers; Layer 4 is
v2-deferred). The Validator is **framework code, not an agent**
(``validation.md §6.1``, ``architecture.md §1.6``): every layer runs
deterministic checks and never invokes an LLM. ADR 0022 records this subpackage's
location.

Phase 1 ships Layer 1 only (static schema + registry reference resolution +
``spec_kind`` discriminator), implemented in ``static_schema_validator`` —
descriptively named per ADR 0026 (the numbered ``layerN`` file/class convention
is rejected; numbered *report keys* like ``layer_1`` are retained). Layers 2/3/5
land in Phase 2 beside it as one descriptively-named module per layer. Each layer
*returns findings*; it never routes — the orchestrator
(``cyberlab_gen.framework.orchestrator``) reads the result and decides what to do
(``validation.md §6.10``, ``architecture.md §1.5``).
"""

from cyberlab_gen.validators.static_schema_validator import (
    PendingProposals,
    StaticSchemaCode,
    StaticSchemaFinding,
    StaticSchemaResult,
    StaticSchemaValidator,
)

__all__ = [
    "PendingProposals",
    "StaticSchemaCode",
    "StaticSchemaFinding",
    "StaticSchemaResult",
    "StaticSchemaValidator",
]
