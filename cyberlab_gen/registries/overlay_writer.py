"""Atomic writer for user-overlay registry files (propose→accept→overlay write).

Architectural source: ``schema.md §4.16`` (the overlay file shape —
``entries:`` + a ``proposals:`` audit map), ADR 0044 (the propose → approve →
overlay → validate loop). This is **mechanical framework code** (no LLM,
``architecture.md §1.5``): given a typed registry entry and a framework-stamped
:class:`ProposalAuditBlock`, it appends (or replaces) the entry in the overlay
file under ``~/.cyberlab-gen/registry-overlay/`` and records the audit block.

The write is **atomic** (write a sibling ``.tmp`` then ``os.replace``) so a crash
mid-write never leaves a half-written, unparseable overlay file. Loading the
existing file before merging means concurrent additions to *different* keys
accumulate rather than clobbering each other across calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.schemas.registries import OverlayRegistryFile

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

    from cyberlab_gen.schemas.registries import ProposalAuditBlock

logger = logging.getLogger(__name__)


def write_overlay_entry[E: BaseModel](
    *,
    overlay_dir: Path,
    registry_filename: str,
    entry_type: type[E],
    entry: E,
    audit: ProposalAuditBlock,
) -> Path:
    """Append/replace ``entry`` in ``<overlay_dir>/<registry_filename>.yaml`` atomically.

    Loads the existing overlay file (empty if absent), replaces any entry sharing
    ``entry``'s registry key (so re-accepting a corrected proposal overwrites rather
    than duplicates), appends otherwise, records ``audit`` in the ``proposals:`` map
    keyed by that registry key, and writes the merged file atomically. Returns the
    written path. The ``proposals``/``entries`` key invariant is enforced by
    :class:`OverlayRegistryFile` at construction.
    """
    overlay_dir.mkdir(parents=True, exist_ok=True)
    path = overlay_dir / f"{registry_filename}.yaml"
    existing = load_overlay_file(path, entry_type)

    # ``ENTRY_KEY_FIELD`` is a ClassVar on each concrete entry type (``name`` for
    # most, ``id`` for external-source / lab-credential entries); the static type
    # here is the ``BaseModel`` bound, so getattr is the right read tool.
    key_field: str = getattr(entry_type, "ENTRY_KEY_FIELD")  # noqa: B009
    key: str = getattr(entry, key_field)

    merged_entries: list[E] = [e for e in existing.entries if getattr(e, key_field) != key]
    merged_entries.append(entry)
    merged_proposals: dict[str, ProposalAuditBlock] = {**existing.proposals, key: audit}

    # ``model_validate`` (not the typed ``__init__``) so the computed ``str`` proposal
    # keys validate to the field's ``RegistryKey`` type without a cast/ignore; entries
    # are already-constructed models, which Pydantic revalidates in place.
    updated = OverlayRegistryFile[entry_type].model_validate(
        {"entries": merged_entries, "proposals": merged_proposals}
    )
    _atomic_write(path, updated.to_yaml())
    logger.info("wrote overlay entry %s to %s (approval=%s)", key, path, audit.approval)
    return path


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling temp file + atomic replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


__all__ = ["write_overlay_entry"]
