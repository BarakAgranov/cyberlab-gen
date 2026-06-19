"""The OSV.dev adapter — honest skip in v1 (its trigger field has no schema home).

OSV's declared trigger (``chain.chain_steps[*].targets.packages[*]``) names a
collection the current AttackSpec does not carry. Rather than pretend to enrich,
the adapter records one honest skip per unresolved trigger naming the missing
field and the owned deferral (ADR 0101). When the package-target schema lands
(Scope B / Phase 3), this adapter gains a real client + parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import LookupPriority

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry


@dataclass(slots=True)
class OsvAdapter:
    """Record an honest skip for each OSV trigger field that has no schema home."""

    source_id: str = "osv_dev"
    priority: LookupPriority = LookupPriority.OTHER

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Skip each unresolved trigger, naming the missing field + the deferral."""
        for trigger in support.unresolved_triggers(entry):
            support.record_skip(
                ctx.result,
                field_path=str(trigger.field),
                source_id=entry.id,
                reason=(
                    f"OSV trigger field {trigger.field!r} has no home in the current AttackSpec "
                    "schema (no package targets); enrichment deferred to the Phase-3 schema work "
                    "(ADR 0101)"
                ),
            )


__all__ = ["OsvAdapter"]
