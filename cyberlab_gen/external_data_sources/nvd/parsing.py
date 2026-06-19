"""Parse the subset of an NVD v2 CVE response enrichment consumes.

Relocated verbatim from ``framework.enrichment`` with the data-driven seam
(ADR 0101). NVD JSON is parsed as ``object``; the ``_as_dict`` / ``_as_object_list``
narrowers keep the parser fully typed under pyright strict without per-line
``# pyright: ignore`` noise.
"""

from __future__ import annotations

from typing import cast

from cyberlab_gen.external_data_sources.types import NvdCveData


def _as_dict(value: object) -> dict[str, object]:
    """Narrow ``value`` to a ``dict[str, object]`` (empty when it is not a dict)."""
    if isinstance(value, dict):
        raw = cast("dict[object, object]", value)
        return {str(k): v for k, v in raw.items()}
    return {}


def _as_object_list(value: object) -> list[object]:
    """Narrow ``value`` to a ``list[object]`` (empty when it is not a list)."""
    if isinstance(value, list):
        return list(cast("list[object]", value))
    return []


def parse_nvd_response(payload: object) -> NvdCveData | None:
    """Parse the subset of an NVD v2 CVE response enrichment uses.

    NVD v2 shape: ``{"vulnerabilities": [{"cve": {"id", "descriptions",
    "weaknesses", "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore",
    "baseSeverity"}}]}}}]}``. Returns ``None`` when no vulnerability is present.
    Tolerant of missing fields (the metric set varies across CVEs).
    """
    vulns = _as_object_list(_as_dict(payload).get("vulnerabilities"))
    if not vulns:
        return None
    cve = _as_dict(_as_dict(vulns[0]).get("cve"))
    if not cve:
        return None

    cve_id = cve.get("id")
    score, severity = _extract_cvss(cve.get("metrics"))
    return NvdCveData(
        cve_id=str(cve_id) if isinstance(cve_id, str) else "",
        cvss_score=score,
        cvss_severity=severity,
        cwe_ids=_extract_cwes(cve.get("weaknesses")),
        description=_extract_description(cve.get("descriptions")),
    )


def _extract_cvss(metrics: object) -> tuple[float | None, str | None]:
    """Pull ``(baseScore, baseSeverity)`` from an NVD ``metrics`` block."""
    metrics_dict = _as_dict(metrics)
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        block = _as_object_list(metrics_dict.get(key))
        if not block:
            continue
        data = _as_dict(_as_dict(block[0]).get("cvssData"))
        raw_score = data.get("baseScore")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else None
        raw_sev = data.get("baseSeverity")
        severity = raw_sev if isinstance(raw_sev, str) else None
        if score is not None or severity is not None:
            return score, severity
    return None, None


def _extract_cwes(weaknesses: object) -> list[str]:
    """Pull CWE ids from an NVD ``weaknesses`` block."""
    out: list[str] = []
    for weakness in _as_object_list(weaknesses):
        for desc in _as_object_list(_as_dict(weakness).get("description")):
            value = _as_dict(desc).get("value")
            if isinstance(value, str) and value.startswith("CWE-"):
                out.append(value)
    return out


def _extract_description(descriptions: object) -> str | None:
    """Pull the English description from an NVD ``descriptions`` block."""
    for desc in _as_object_list(descriptions):
        desc_dict = _as_dict(desc)
        if desc_dict.get("lang") == "en":
            value = desc_dict.get("value")
            if isinstance(value, str):
                return value
    return None


__all__ = ["parse_nvd_response"]
