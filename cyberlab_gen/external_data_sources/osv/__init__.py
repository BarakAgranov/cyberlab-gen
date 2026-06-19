"""OSV.dev adapter — cross-ecosystem package-advisory enrichment (honest skip in v1).

``registry-details.md §4.2`` (the ``osv_dev`` entry). OSV's trigger fires on
``chain.chain_steps[*].targets.packages[*]`` — a field the current AttackSpec
schema **does not have** (the Extractor records no package targets). The adapter
is wired into the data-driven seam and resolves its trigger, but with no
resolvable field it records an honest skip rather than enriching nothing
silently. Adding the package-target schema fields (and the Extractor authorship
that fills them) is an **owned deferral** to the Phase-3 schema work / Scope B
(ADR 0101).
"""

from cyberlab_gen.external_data_sources.osv.adapter import OsvAdapter

__all__ = ["OsvAdapter"]
