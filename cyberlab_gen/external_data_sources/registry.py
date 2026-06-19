"""The adapter registration table — the data-driven dispatch (``pipeline.md §3.2.4``).

``framework.enrichment.enrich()`` iterates the ``external_data_sources`` registry
entries and resolves a ``SourceAdapter`` per entry through ``resolve_adapter`` —
**no more hardcoded ``_NVD_SOURCE_ID`` / ``_MITRE_SOURCE_ID`` dispatch** (the
``dev/phase-2-seams.md`` ③.2 requirement; ADR 0101). A registry entry with no
registered adapter is run as an honest stub-skip by the driver.

Each adapter is a stateless dataclass, so one shared instance per source id is
safe to reuse across runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.external_data_sources.bulletins import BulletinAdapter
from cyberlab_gen.external_data_sources.epss import EpssAdapter
from cyberlab_gen.external_data_sources.kev import KevAdapter
from cyberlab_gen.external_data_sources.mitre import MitreAdapter
from cyberlab_gen.external_data_sources.msrc import MsrcAdapter
from cyberlab_gen.external_data_sources.nvd import NvdAdapter
from cyberlab_gen.external_data_sources.osv import OsvAdapter

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources.ports import SourceAdapter

#: Source id → adapter. Keys are the ``external_data_sources`` registry entry ids
#: (``registry-details.md §4.2``). The bulletin family shares one adapter
#: parametrised by source id at run time.
_TABLE: dict[str, SourceAdapter] = {
    "nvd": NvdAdapter(),
    "mitre_attack": MitreAdapter(),
    "cisa_kev": KevAdapter(),
    "epss": EpssAdapter(),
    "msrc": MsrcAdapter(),
    "osv_dev": OsvAdapter(),
    "aws_security_bulletins": BulletinAdapter(source_id="aws_security_bulletins"),
    "azure_security_advisories": BulletinAdapter(source_id="azure_security_advisories"),
    "gcp_security_bulletins": BulletinAdapter(source_id="gcp_security_bulletins"),
}


def resolve_adapter(source_id: str) -> SourceAdapter | None:
    """Return the adapter for ``source_id``, or ``None`` when none is registered."""
    return _TABLE.get(source_id)


def registered_source_ids() -> frozenset[str]:
    """The source ids that have a registered adapter (the default integrated set)."""
    return frozenset(_TABLE)


__all__ = ["registered_source_ids", "resolve_adapter"]
