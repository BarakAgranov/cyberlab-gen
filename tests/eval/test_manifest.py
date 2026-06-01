"""Tests for the blog-set manifest loader (ADR 0014 shape, ADR 0025 loader).

Covers the happy path (the bundled manifest loads), the forward-compat invariant
(every ``walk:`` path resolves — ADR 0014), and loader discipline on missing /
malformed files (``CLAUDE.md`` loader rules).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from eval.runner.cli import check_walks_resolve
from eval.runner.manifest import (
    TBD,
    BlogSetManifest,
    load_manifest,
    repo_root,
    walk_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_bundled_manifest_loads_and_has_3_to_5_curated_blogs() -> None:
    manifest = load_manifest()
    assert manifest.spec_kind == "BlogSetManifest"
    # implementation-plan.md §4.3: Phase 1 grows the curated set to 3-5 blogs.
    assert 3 <= len(manifest.curated) <= 5
    assert manifest.held_out == []  # rotation lands Phase 4


def test_bundled_manifest_includes_a_long_blog() -> None:
    # Task-8 brief: include at least one long blog to exercise chunking.
    manifest = load_manifest()
    tags = {tag for e in manifest.curated for tag in e.coverage_tags}
    assert "long_blog:chunking" in tags


def test_every_walk_path_resolves() -> None:
    # ADR 0014 forward-compat: a manifest entry must not point at a missing walk.
    manifest = load_manifest()
    assert check_walks_resolve(manifest) == []
    for entry in manifest.curated:
        assert walk_path(entry).is_file()


def test_all_ids_and_entry_lookup() -> None:
    manifest = load_manifest()
    ids = manifest.all_ids()
    assert "long-multi-stage-cloud-campaign" in ids
    entry = manifest.entry("long-multi-stage-cloud-campaign")
    assert entry.shape == "aws_ttp"
    # the synthetic long-blog fixture has no resolved URL
    assert entry.url == TBD
    assert not entry.url_is_resolved()
    with pytest.raises(KeyError):
        manifest.entry("does-not-exist")


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.yaml")


def test_load_malformed_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.yaml"
    # wrong spec_kind + missing rotation_generation → schema-invalid
    bad.write_text("spec_version: 1\nspec_kind: NotAManifest\ncurated: []\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(bad)


def test_extra_field_is_forbidden(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.yaml"
    bad.write_text(
        "spec_version: 1\nspec_kind: BlogSetManifest\nrotation_generation: 0\n"
        "curated: []\nheld_out: []\nbogus: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_manifest(bad)


def test_round_trips_through_yaml() -> None:
    manifest = load_manifest()
    again = BlogSetManifest.from_yaml(manifest.to_yaml())
    assert again == manifest


def test_repo_root_contains_eval_and_package() -> None:
    root = repo_root()
    assert (root / "eval").is_dir()
    assert (root / "cyberlab_gen").is_dir()
