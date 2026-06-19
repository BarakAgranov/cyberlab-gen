"""MITRE ATT&CK adapter — local-catalog technique enrichment (no live call, no budget).

``registry-details.md §4.2`` (the ``mitre_attack`` entry); ADR 0055/0058. The
bundled seed is consulted, **not** used as a validation gate: a seed-listed id is
enriched; a well-formed uncatalogued id is left *unverified* (an honest skip),
never a false "contradicting technique" discrepancy — the 8-entry seed is not an
authority. A live MITRE adapter is later work (findings doc 0001 §5).
"""

from cyberlab_gen.external_data_sources.mitre.adapter import MitreAdapter

__all__ = ["MitreAdapter"]
