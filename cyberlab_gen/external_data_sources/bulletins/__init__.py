"""Security-bulletin adapter — AWS / Azure / GCP RSS feed enrichment.

``registry-details.md §4.2`` (the ``aws_security_bulletins`` /
``azure_security_advisories`` / ``gcp_security_bulletins`` entries). One adapter,
parametrised by source id, drives all three feeds. Triggered by a
``facets[?value='target:<cloud>']`` predicate: when the spec targets the cloud,
recent bulletin items are recorded as lab-level context (``BulletinRecord``) in
the audit channel — no per-CVE key, no typed AttackSpec home (ADR 0101). AWS/GCP
feeds are ``best_effort``: their unavailability never halts (ADR 0042).
"""

from cyberlab_gen.external_data_sources.bulletins.adapter import (
    BulletinAdapter,
    HttpxBulletinClient,
    parse_rss_feed,
)

__all__ = ["BulletinAdapter", "HttpxBulletinClient", "parse_rss_feed"]
