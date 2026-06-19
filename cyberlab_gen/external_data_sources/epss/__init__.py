"""EPSS adapter — Exploit-Prediction-Scoring-System enrichment.

``registry-details.md §4.2`` (the ``epss`` entry). EPSS gives a CVE a
probability-of-exploitation score; an additive signal with no blog counterpart,
so the ``EpssRecord`` lives in the audit channel (ADR 0101).
"""

from cyberlab_gen.external_data_sources.epss.adapter import (
    EpssAdapter,
    HttpxEpssClient,
    parse_epss_response,
)

__all__ = ["EpssAdapter", "HttpxEpssClient", "parse_epss_response"]
