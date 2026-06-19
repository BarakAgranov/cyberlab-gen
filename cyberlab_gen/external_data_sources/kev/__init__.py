"""CISA KEV adapter — Known-Exploited-Vulnerabilities catalog enrichment.

``registry-details.md §4.2`` (the ``cisa_kev`` entry). The catalog is downloaded
once and queried locally; a CVE present in KEV gets a ``KevRecord`` (an additive
"actively exploited" signal). No blog field corroborates KEV, so the record lives
in the audit channel, not on ``CveReference`` (ADR 0101).
"""

from cyberlab_gen.external_data_sources.kev.adapter import (
    HttpxKevClient,
    KevAdapter,
    parse_kev_catalog,
)

__all__ = ["HttpxKevClient", "KevAdapter", "parse_kev_catalog"]
