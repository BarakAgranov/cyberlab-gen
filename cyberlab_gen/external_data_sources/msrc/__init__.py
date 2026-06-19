"""MSRC adapter — Microsoft Security Response Center CVRF enrichment.

``registry-details.md §4.2`` (the ``msrc`` entry). MSRC is authoritative for
Microsoft-issued CVEs (affected products, fix versions). Its CVRF data is an
additive signal (no blog field corroborates the product/fix detail in the current
schema), so the ``MsrcRecord`` lives in the audit channel (ADR 0101). The
registry's Microsoft-only trigger predicate has no schema signal to resolve, so
the adapter queries every CVE and the client returns ``None`` for non-Microsoft
CVEs (the server-side filter).
"""

from cyberlab_gen.external_data_sources.msrc.adapter import (
    HttpxMsrcClient,
    MsrcAdapter,
    parse_msrc_cvrf,
)

__all__ = ["HttpxMsrcClient", "MsrcAdapter", "parse_msrc_cvrf"]
