"""Loader for the curated/held-out blog-set manifest (``eval/blog-sets/manifest.yaml``).

Architectural source: ``eval.md §7.3`` (blog set composition, rotation,
coverage tagging). The *shape* is invented in **ADR 0014** (authoritative — not
yet promoted into ``eval.md``); this loader reads exactly that shape. The
Phase-1 harness consumes the manifest to drive per-blog runs
(``implementation-plan.md §4.2``); ADR 0025 records the loader decision.

Per ADR 0014 the manifest is an envelope (``spec_version`` / ``spec_kind``) plus
a ``rotation_generation`` integer and two ordered lists (``curated`` /
``held_out``) of entries, each with ``id`` / ``shape`` / ``url`` / ``title`` /
``publisher`` / ``accessed_date`` / ``walk`` / ``coverage_tags``. Phase-1 entries
may carry ``TBD`` for not-yet-resolved blog metadata (the synthetic long-blog
fixture has no live URL); the loader accepts ``TBD`` as a sentinel string rather
than rejecting it, because the manifest is the source of truth for *what's in the
set*, not for live-fetch readiness (ADR 0014 field rationale).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator
from ruamel.yaml import YAML

from cyberlab_gen.schemas.base import ArtifactModel

#: Sentinel for a manifest field not yet resolved to a real value (ADR 0014:
#: ``url`` / ``title`` / ``publisher`` / ``accessed_date`` may be ``TBD`` until a
#: human picks the blog). Distinguished from a missing field (which is an error).
TBD = "TBD"

#: The repo-root-relative default manifest path (``eval.md §7.3``).
DEFAULT_MANIFEST_RELPATH = "eval/blog-sets/manifest.yaml"


class BlogEntry(ArtifactModel):
    """One blog in the curated or held-out set (ADR 0014 per-entry shape).

    ``id`` is the stable slug (also the walk-file slug). ``shape`` is open-set
    (ADR 0014: v0.2+ may add shapes) so it is a plain string, not an enum.
    ``walk`` is a repo-root-relative path to the ground-truth walk; a smoke test
    enforces that it resolves (``tests/eval/test_manifest.py``). Metadata fields
    accept the ``TBD`` sentinel.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    shape: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    accessed_date: str = Field(min_length=1)
    walk: str = Field(min_length=1)
    coverage_tags: list[str] = Field(default_factory=list[str])

    @field_validator("accessed_date", mode="before")
    @classmethod
    def _coerce_date_to_iso(cls, v: object) -> object:
        """Accept a bare YAML date (``2026-05-20``) as well as the ``TBD`` string.

        YAML safe-load types an unquoted ISO date as a ``date``; ADR 0014 specifies
        the field as ``ISO 8601 date | TBD``. Coerce a ``date`` / ``datetime`` to
        its ISO string so both the quoted-string and bare-date YAML forms load, and
        the ``TBD`` sentinel passes through untouched.
        """
        if isinstance(v, datetime):
            return v.date().isoformat()
        if isinstance(v, date):
            return v.isoformat()
        return v

    def url_is_resolved(self) -> bool:
        """True when this entry points at a live, fetchable URL (not the ``TBD`` sentinel)."""
        return self.url != TBD


class BlogSetManifest(ArtifactModel):
    """The parsed ``manifest.yaml`` (ADR 0014).

    ``spec_kind`` is pinned to ``BlogSetManifest`` so a future loader can dispatch
    on it (ADR 0014). ``curated`` / ``held_out`` are separate ordered lists; an
    entry is in exactly one list per rotation generation. Phase 1 ships an empty
    ``held_out`` (rotation lands Phase 4, ``implementation-plan.md §1.6``).
    """

    model_config = ConfigDict(frozen=True)

    spec_version: int = Field(ge=1)
    spec_kind: Literal["BlogSetManifest"] = "BlogSetManifest"
    rotation_generation: int = Field(ge=0)
    curated: list[BlogEntry] = Field(default_factory=list[BlogEntry])
    held_out: list[BlogEntry] = Field(default_factory=list[BlogEntry])

    def entry(self, blog_id: str) -> BlogEntry:
        """Return the entry with ``id == blog_id`` from either set, or raise ``KeyError``."""
        for e in (*self.curated, *self.held_out):
            if e.id == blog_id:
                return e
        raise KeyError(f"no blog entry with id {blog_id!r} in the manifest")

    def all_ids(self) -> list[str]:
        """Every blog id across both sets, curated first (manifest order)."""
        return [e.id for e in (*self.curated, *self.held_out)]


def repo_root() -> Path:
    """The repository root (the directory that holds ``eval/`` and ``cyberlab_gen/``).

    This module lives at ``<root>/eval/runner/manifest.py``, so the root is two
    parents up. Used to resolve the default manifest path and the per-entry
    ``walk:`` paths, which are repo-root-relative (ADR 0014).
    """
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(path: Path | None = None) -> BlogSetManifest:
    """Load and validate the blog-set manifest.

    Defaults to ``<repo-root>/eval/blog-sets/manifest.yaml``. Raises
    ``FileNotFoundError`` on a missing file and ``pydantic.ValidationError`` on a
    malformed one (so the smoke test and any harness run fail loudly rather than
    proceeding against a broken set — loader discipline, ``CLAUDE.md``).
    """
    target = path if path is not None else repo_root() / DEFAULT_MANIFEST_RELPATH
    if not target.is_file():
        raise FileNotFoundError(f"blog-set manifest not found at {target}")
    yaml = YAML(typ="safe")
    data = yaml.load(target.read_text(encoding="utf-8"))
    return BlogSetManifest.model_validate(data)


def walk_path(entry: BlogEntry, *, root: Path | None = None) -> Path:
    """Resolve a blog entry's repo-root-relative ``walk:`` path to an absolute path."""
    base = root if root is not None else repo_root()
    return base / entry.walk


__all__ = [
    "DEFAULT_MANIFEST_RELPATH",
    "TBD",
    "BlogEntry",
    "BlogSetManifest",
    "load_manifest",
    "repo_root",
    "walk_path",
]
