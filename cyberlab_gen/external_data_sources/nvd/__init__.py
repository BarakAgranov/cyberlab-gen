"""NVD adapter — the first concrete external-data adapter behind the seam.

``registry-details.md §4.2`` (the ``nvd`` entry); ``pipeline.md §3.2.4``. NVD is
authoritative for CVE metadata (CVSS, severity) — the one source whose values are
**corroboration-capable** (the blog can claim a CVSS/severity and enrichment
checks it for a discrepancy), which is why they live as typed provenance on
``CveReference`` rather than in the additive audit channel (ADR 0101).
"""

from cyberlab_gen.external_data_sources.nvd.adapter import (
    HttpxNvdClient,
    NvdAdapter,
)
from cyberlab_gen.external_data_sources.nvd.parsing import parse_nvd_response

__all__ = ["HttpxNvdClient", "NvdAdapter", "parse_nvd_response"]
