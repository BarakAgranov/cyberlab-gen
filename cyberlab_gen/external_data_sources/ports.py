"""Neutral ports for external-data enrichment (the relocated client surface).

This is the ``dev/phase-2-seams.md`` ④ / ADR 0077 relocation: the ``NvdClient``
Protocol used to live in ``framework.enrichment``, but agents *and* validators
import it — a leaf of the framework subpackage that everything reaches into. It
now lives here, a neutral ports module under ``external_data_sources`` that
depends only on ``types`` + ``schemas`` (no ``framework`` import), so the
adapters, the driver, the agents, and the validators all import *down* into one
place with no cycle (``coding-conventions.md §3.3``).

The per-source client Protocols (``KevClient`` / ``EpssClient`` / ``MsrcClient`` /
``BulletinClient``) are the narrow surfaces each adapter needs. Injecting them
(rather than constructing ``httpx`` clients inline) keeps enrichment testable:
tests pass fakes that return recorded fixtures or raise
``ExternalApiUnavailableError`` to exercise the never-fatal degrade path
(ADR 0042). The ``SourceAdapter`` Protocol + ``EnrichmentContext`` are the seam
the data-driven driver iterates (``pipeline.md §3.2.4``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.types import (
        BulletinRecord,
        EnrichmentResult,
        EpssRecord,
        KevRecord,
        LookupPriority,
        MsrcRecord,
        NvdCveData,
    )
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.registries import (
        ExternalDataSourceEntry,
        MitreTechniqueCatalog,
    )


# --- per-source client Protocols -------------------------------------------


class NvdClient(Protocol):
    """The narrow client surface enrichment needs from NVD.

    Tests pass a fake that returns recorded fixtures or raises
    ``ExternalApiRateLimitError`` / ``ExternalApiUnavailableError`` to exercise
    the degrade path.
    """

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        """Return parsed CVE data, or ``None`` when NVD has no record.

        Raises ``ExternalApiRateLimitError`` when rate-limited and
        ``ExternalApiUnavailableError`` when otherwise unreachable; the caller
        records the skip and continues (ADR 0042).
        """
        ...


class KevClient(Protocol):
    """Narrow surface for the CISA KEV catalog (downloaded once, queried locally)."""

    def lookup(self, cve_id: str) -> KevRecord | None:
        """Return the KEV entry for ``cve_id`` or ``None`` when not listed."""
        ...


class EpssClient(Protocol):
    """Narrow surface for the EPSS score API."""

    def lookup(self, cve_id: str) -> EpssRecord | None:
        """Return the EPSS record for ``cve_id`` or ``None`` when unscored."""
        ...


class MsrcClient(Protocol):
    """Narrow surface for the MSRC CVRF API (Microsoft-issued CVEs)."""

    def lookup(self, cve_id: str) -> MsrcRecord | None:
        """Return MSRC CVRF data for ``cve_id`` or ``None`` when not Microsoft-issued."""
        ...


class BulletinClient(Protocol):
    """Narrow surface for a security-bulletin RSS feed."""

    def list_recent(self) -> list[BulletinRecord]:
        """Return recent bulletin items (may be empty); raises on unavailability."""
        ...


# --- client bundle + adapter context ---------------------------------------


@dataclass(slots=True)
class SourceClients:
    """The injectable external-source clients, one optional slot per integrated source.

    A ``None`` slot means the source has no live client this run (the hermetic
    default) — its adapter records an honest skip rather than enriching.
    ``bulletins`` is keyed by source id (AWS / Azure / GCP have distinct feeds).
    """

    nvd: NvdClient | None = None
    kev: KevClient | None = None
    epss: EpssClient | None = None
    msrc: MsrcClient | None = None
    bulletins: dict[str, BulletinClient] = field(default_factory=dict[str, "BulletinClient"])


@dataclass(slots=True)
class EnrichmentContext:
    """The state an adapter mutates during one enrichment pass.

    ``budget`` is a single-element list used as a mutable counter shared across
    adapters (CVEs spend first per priority order). The driver owns construction;
    adapters only read ``spec`` / ``clients`` / ``mitre_catalog`` and append to
    ``result`` + decrement ``budget``.
    """

    spec: AttackSpec
    result: EnrichmentResult
    budget: list[int]
    clients: SourceClients
    mitre_catalog: MitreTechniqueCatalog


class SourceAdapter(Protocol):
    """One external-data source behind the data-driven enrichment seam.

    The driver resolves an adapter per ``external_data_sources`` entry and runs it
    in ``priority`` order. ``enrich`` reads the entry's ``enrichment_triggers``,
    resolves them against the spec, performs budget-aware lookups via the injected
    client, and appends results / honest skips to ``ctx.result``. It never routes,
    never raises on an unavailable source, and never lets an LLM decide anything
    (``architecture.md §1.5/§1.6``).
    """

    source_id: str
    priority: LookupPriority

    def enrich(self, ctx: EnrichmentContext, entry: ExternalDataSourceEntry) -> None: ...


__all__ = [
    "BulletinClient",
    "EnrichmentContext",
    "EpssClient",
    "KevClient",
    "MsrcClient",
    "NvdClient",
    "SourceAdapter",
    "SourceClients",
]
