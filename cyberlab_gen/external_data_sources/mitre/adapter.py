"""The MITRE ATT&CK adapter (local catalog; no live call, no budget).

Encapsulates the technique enrichment that used to live inline in
``framework.enrichment`` (``_enrich_techniques`` / ``_collect_technique_refs``),
now behind the ``SourceAdapter`` seam (ADR 0101). Reads ``ctx.mitre_catalog``
(the bundled seed); does not consume the external-call budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberlab_gen.external_data_sources import support
from cyberlab_gen.external_data_sources.types import LookupPriority

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import EnrichmentContext
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.registries import ExternalDataSourceEntry


def _collect_technique_refs(spec: AttackSpec) -> list[str]:
    """Gather every MITRE technique id referenced in the AttackSpec (de-duped).

    Covers the chain-step ``techniques.mitre[*]`` trigger and the standalone
    ``external_references.mitre_techniques[*]`` references.
    """
    seen: dict[str, None] = {}
    if spec.chain is not None:
        for step in spec.chain.chain_steps:
            for tech in step.techniques.mitre:
                seen.setdefault(tech, None)
    if spec.external_references is not None:
        for ref in spec.external_references.mitre_techniques:
            seen.setdefault(ref.technique_id, None)
    return list(seen.keys())


@dataclass(slots=True)
class MitreAdapter:
    """Enrich MITRE technique ids against the bundled local seed."""

    source_id: str = "mitre_attack"
    priority: LookupPriority = LookupPriority.MITRE

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None:
        """Enrich technique ids against the local seed (seed-listed → enriched, else unverified).

        A well-formed id absent from the seed is recorded as an UNVERIFIED skip
        (ADR 0055/0058 P2) — never a false "contradicting technique" discrepancy,
        because the 8-entry seed is not an authority and would mislabel real,
        current ATT&CK ids (T1195/T1199/…).
        """
        tech_refs = _collect_technique_refs(ctx.spec)
        if not tech_refs:
            return

        known = {t.name: t.display_name for t in ctx.mitre_catalog.entries}
        for tech in tech_refs:
            path = f"technique.{tech}"
            if tech in known:
                ctx.result.enriched_field_paths.append(path)
            else:
                support.record_skip(
                    ctx.result,
                    field_path=path,
                    source_id=entry.id,
                    reason=(
                        f"MITRE technique {tech} not in the bundled seed and no MITRE adapter "
                        "is wired this phase; left unverified (requires external research)"
                    ),
                )


__all__ = ["MitreAdapter"]
