"""Typed records the external-data adapters produce (the neutral types layer).

This module is the **neutral home** for the data the pre-Planner enrichment pass
(``pipeline.md §3.2.4``) produces. It depends only on ``schemas`` — never on
``framework`` — so the per-source adapters under
``cyberlab_gen/external_data_sources/<id>/`` and the ``framework.enrichment``
driver can both import it without a cycle (``coding-conventions.md §3.3``; the
``NvdClient``-Protocol relocation tracked in ``dev/phase-2-seams.md`` ④ / ADR 0077,
landed in ADR 0101).

The records (``KevRecord`` / ``EpssRecord`` / ``MsrcRecord`` / ``BulletinRecord``)
model the **documented publisher response shapes** losslessly for the fields the
system consumes — they are *additive* enrichment signals with no blog counterpart
to corroborate, so they live in the enrichment audit channel
(``EnrichmentResult``), **not** as new typed ``AttackSpec`` fields. Committing them
to the versioned, boundary-crossing AttackSpec contract is deferred to the
Phase-3 Generator (the first real consumer, which defines the consumed shape) —
ADR 0101 §"schema-home gap". The corroboration-capable NVD CVSS/severity values
*do* live on ``CveReference`` because the blog can claim them and enrichment
checks for a discrepancy; the additive sources cannot disagree, so there is
nothing to fabricate.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import Field

from cyberlab_gen.schemas.attack_spec import MaterialDiscrepancy
from cyberlab_gen.schemas.base import InternalModel


class LookupPriority(IntEnum):
    """Budget-spend priority order (``pipeline.md §3.2.4``).

    Lower value = spent first: CVEs > MITRE > GitHub > bulletins > other. The
    secondary CVE-keyed sources (KEV/EPSS/MSRC) rank ``OTHER`` — NVD is the
    authoritative CVE-metadata call and spends first; the rest are additive.
    (MITRE is local and does not consume the call budget, but the ordering is
    preserved so the budget walker stays correct when MITRE becomes live.)
    """

    CVE = 0
    MITRE = 1
    GITHUB = 2
    BULLETIN = 3
    OTHER = 4


class CveResolution(StrEnum):
    """Per-CVE outcome of the framework's NVD enrichment call.

    Consumed by the orchestrator-owned grounding stack's CVE ship-gate
    (``validation.md §6.10.2``) so the gate can verify grounded CVE ids against
    NVD **without doing its own network I/O** — enrichment is the network pass,
    the validator stays no-network (``architecture.md §1.6``). ``UNAVAILABLE``
    (rate-limited / source down) is *never* a hallucination: the gate skips it
    (ADR 0042). ADR 0101.
    """

    CONFIRMED = "confirmed"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"


# --- NVD ---------------------------------------------------------------------


class NvdCveData(InternalModel):
    """The CVE metadata enrichment reads from an NVD response.

    Internal (``InternalModel``) — it never crosses a pipeline-stage boundary; it
    is parsed from the NVD JSON and consumed in-process. ``extra="ignore"`` lets
    the parser pick the few fields used out of NVD's large payload.
    """

    cve_id: str
    cvss_score: float | None = None
    cvss_severity: str | None = None
    cwe_ids: list[str] = Field(default_factory=list[str])
    description: str | None = None


# --- CISA KEV ----------------------------------------------------------------


class KevRecord(InternalModel):
    """One CISA Known-Exploited-Vulnerabilities catalog entry (the documented shape).

    Additive "this CVE is actively exploited" signal; no blog field corroborates
    it, so it lives in the audit channel (ADR 0101). Fields mirror the published
    KEV catalog JSON entry.
    """

    source_id: str
    cve_id: str
    vendor_project: str | None = None
    product: str | None = None
    vulnerability_name: str | None = None
    date_added: str | None = None
    short_description: str | None = None
    required_action: str | None = None
    due_date: str | None = None
    known_ransomware_campaign_use: str | None = None
    notes: str | None = None
    cwes: list[str] = Field(default_factory=list[str])


# --- EPSS --------------------------------------------------------------------


class EpssRecord(InternalModel):
    """One EPSS score record (``api.first.org/data/v1/epss``).

    ``epss`` is the probability-of-exploitation-in-30-days score; ``percentile``
    its rank; ``as_of`` the score date. Additive signal — audit channel.
    """

    source_id: str
    cve_id: str
    epss: float
    percentile: float
    as_of: str | None = None


# --- MSRC (CVRF) -------------------------------------------------------------


class MsrcRemediation(InternalModel):
    """One MSRC CVRF remediation (fix) entry."""

    product: str | None = None
    fixed_build: str | None = None
    description: str | None = None
    url: str | None = None


class MsrcRecord(InternalModel):
    """MSRC CVRF data for a Microsoft-issued CVE (the consumed subset).

    Models the load-bearing CVRF nesting: the affected-product list and the
    remediation (fix-version) entries the ``discrepancy_materiality_rules`` name
    (``affected_products``, ``fix_version``), plus the CVSS score set. Additive —
    audit channel (ADR 0101).
    """

    source_id: str
    cve_id: str
    title: str | None = None
    affected_products: list[str] = Field(default_factory=list[str])
    remediations: list[MsrcRemediation] = Field(default_factory=list[MsrcRemediation])
    cvss_score: float | None = None
    cvss_vector: str | None = None


# --- Security bulletins (RSS) ------------------------------------------------


class BulletinRecord(InternalModel):
    """One security-bulletin RSS item (AWS / Azure / GCP feeds).

    Lab-level context with no per-CVE key and no typed AttackSpec home; recorded
    in the audit channel. The source is ``best_effort`` for AWS/GCP — its
    unavailability never halts (ADR 0042).
    """

    source_id: str
    title: str
    link: str | None = None
    published: str | None = None
    summary: str | None = None


# --- Enrichment account ------------------------------------------------------


class SkippedLookup(InternalModel):
    """One lookup the framework chose not to (or could not) perform.

    Carries the ``unknown_from_blog``-style reason naming why (budget exhausted,
    rate limited, source unavailable, source not integrated, trigger field does
    not resolve in this schema version, or the authority has no record). Surfaced
    in the run report so the gap is honest (``implementation-plan.md §4.2``).
    """

    field_path: str
    source_id: str
    reason: str


class EnrichmentResult(InternalModel):
    """The framework's account of an enrichment pass.

    The pass mutates the AttackSpec in place — it rewrites a corroboration-capable
    field's ``Provenance`` to ``external_api`` (NVD CVSS/severity) and appends to
    ``material_discrepancies`` — and returns this account for the run report:
    which field paths it enriched, the material/non-material discrepancies it
    found, the additive typed records the secondary sources produced, and the
    lookups it skipped. ``calls_made`` counts external (budget-consuming) calls
    only. ``cve_resolution`` carries the per-CVE NVD outcome the grounding
    ship-gate consumes (ADR 0101).
    """

    enriched_field_paths: list[str] = Field(default_factory=list[str])
    material_discrepancies: list[MaterialDiscrepancy] = Field(
        default_factory=list[MaterialDiscrepancy]
    )
    non_material_field_paths: list[str] = Field(default_factory=list[str])
    skipped: list[SkippedLookup] = Field(default_factory=list[SkippedLookup])
    calls_made: int = 0

    # Additive typed records from the secondary sources (audit channel; ADR 0101).
    kev_records: list[KevRecord] = Field(default_factory=list[KevRecord])
    epss_records: list[EpssRecord] = Field(default_factory=list[EpssRecord])
    msrc_records: list[MsrcRecord] = Field(default_factory=list[MsrcRecord])
    bulletin_records: list[BulletinRecord] = Field(default_factory=list[BulletinRecord])

    # Per-CVE NVD outcome for the grounding ship-gate (ADR 0101). A CVE absent
    # here was never NVD-checked (no client) — the honest "couldn't check" posture.
    cve_resolution: dict[str, CveResolution] = Field(default_factory=dict[str, CveResolution])


__all__ = [
    "BulletinRecord",
    "CveResolution",
    "EnrichmentResult",
    "EpssRecord",
    "KevRecord",
    "LookupPriority",
    "MsrcRecord",
    "MsrcRemediation",
    "NvdCveData",
    "SkippedLookup",
]
