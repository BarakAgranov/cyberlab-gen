"""Mechanical discrepancy-materiality classification (``pipeline.md §3.2.4``).

A blog-vs-API disagreement is *always recorded*; a **material** one (cross-tier
CVSS, different vector/CWE/affected-products) additionally surfaces — at the
post-Extractor interrupt in Phase 4 — while a **non-material** one (same-tier
CVSS, wording) is rewritten silently. The classification is **pure rule lookup**
against each source entry's ``discrepancy_materiality_rules`` — never an LLM
judgment (``architecture.md §1.6``: mechanical safety checks are never
LLM-based).

This is a neutral helper layer (depends only on ``schemas``); the per-source
adapters import it. Relocated from ``framework.enrichment`` with the data-driven
seam (ADR 0101).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.schemas.enums import Severity

if TYPE_CHECKING:
    from cyberlab_gen.schemas.registries import (
        DiscrepancyMaterialityRule,
        ExternalDataSourceEntry,
    )

#: CVSS qualitative-severity tiers, ordered low→high. Same-tier CVSS differences
#: are non-material; cross-tier differences are material (``pipeline.md §3.2.4``).
_CVSS_TIERS: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def materiality_rule(
    entry: ExternalDataSourceEntry, field_path: str
) -> DiscrepancyMaterialityRule | None:
    """Return the materiality rule whose ``field_path`` matches, if any."""
    for rule in entry.discrepancy_materiality_rules:
        if rule.field_path == field_path:
            return rule
    return None


def classify(entry: ExternalDataSourceEntry, rule_field: str) -> str:
    """Classify a discrepancy on ``rule_field`` per the entry's rules.

    Returns ``"material"`` or ``"non_material"``. Default when no rule names the
    field: **material** — the conservative reading (``pipeline.md §3.2.4``: "the
    framework never silently resolves a disagreement that would change the lab's
    character"). An unclassified field is treated as character-changing until a
    rule says otherwise.
    """
    rule = materiality_rule(entry, rule_field)
    if rule is None:
        return "material"
    return rule.classification


def cvss_tier(severity: str) -> int | None:
    """Map a qualitative CVSS severity string to its tier ordinal, if known."""
    return _CVSS_TIERS.get(severity.strip().lower())


def severity_from_cvss(severity: str) -> Severity | None:
    """Map an NVD qualitative severity to the closed ``Severity`` enum, if it maps."""
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
    }
    return mapping.get(severity.strip().lower())


def severity_materiality(blog: Severity, api: Severity) -> str:
    """Classify a blog-vs-API severity difference by CVSS tier.

    Same tier → non-material; cross-tier → material. ``Severity`` members map
    directly onto CVSS qualitative tiers.
    """
    blog_tier = cvss_tier(str(blog))
    api_tier = cvss_tier(str(api))
    if blog_tier is None or api_tier is None:
        return "material"
    return "non_material" if blog_tier == api_tier else "material"


__all__ = [
    "classify",
    "cvss_tier",
    "materiality_rule",
    "severity_from_cvss",
    "severity_materiality",
]
