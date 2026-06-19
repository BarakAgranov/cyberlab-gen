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
    attack_spec_path,
    load_manifest,
    repo_root,
    walk_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_bundled_manifest_loads_and_has_8_to_10_curated_blogs() -> None:
    manifest = load_manifest()
    assert manifest.spec_kind == "BlogSetManifest"
    # implementation-plan.md §5.3 (Task 10): Phase 2 grows the curated set to 8-10 blogs.
    assert 8 <= len(manifest.curated) <= 10
    assert manifest.held_out == []  # rotation lands Phase 4


def test_bundled_manifest_includes_a_long_blog() -> None:
    # Task-8 brief: include at least one long blog to exercise chunking.
    manifest = load_manifest()
    tags = {tag for e in manifest.curated for tag in e.coverage_tags}
    assert "long_blog:chunking" in tags


def test_curated_set_covers_the_four_required_phase2_dimensions() -> None:
    # implementation-plan.md §5.3 / Task 10: the grown set must exercise multi-cloud, a substantive
    # vulnerability_story, a `mixed` reproducibility example, and a runtime:* Planner-proposal
    # trigger (a non-first-class runtime). Asserted on coverage_tags so the set can't silently lose a
    # required dimension as it evolves.
    tags = {tag for e in load_manifest().curated for tag in e.coverage_tags}
    assert "multi_cloud" in tags  # at least one genuinely multi-cloud blog
    assert "vulnerability_disclosure:present" in tags  # substantive vulnerability_story
    assert "mixed_reproducibility" in tags  # a mixed lab-level classification example
    assert (
        "non_first_class_runtime" in tags
    )  # the runtime:* proposal trigger (e.g. runtime:netlify)


def test_curated_set_diversified_beyond_aws() -> None:
    # The Phase-1 set was 100% AWS; Phase-2 growth adds Azure + GCP coverage (eval.md §7.3).
    tags = {tag for e in load_manifest().curated for tag in e.coverage_tags}
    assert {"cloud:aws", "cloud:azure", "cloud:gcp"} <= tags


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


# --- plan-eval attack_spec input (ADR 0102) ---------------------------------


def test_only_codebuild_has_a_committed_attack_spec_fixture() -> None:
    # ADR 0102: exactly one blog ships a committed attack_spec fixture today (the runnable plan-eval
    # demo input); the rest resolve to None (skipped in a provider-backed plan run until extracted).
    manifest = load_manifest()
    resolved = {e.id for e in manifest.curated if e.attack_spec_is_resolved()}
    assert resolved == {"aws-codebuild-actor-id-regex-bypass"}
    # and its path resolves to a real file on disk.
    entry = manifest.entry("aws-codebuild-actor-id-regex-bypass")
    path = attack_spec_path(entry)
    assert path is not None and path.is_file()
    # a blog without an attack_spec resolves to None.
    assert (
        attack_spec_path(manifest.entry("entra-id-actor-token-cross-tenant-global-admin")) is None
    )


def test_codebuild_attack_spec_fixture_is_schema_current() -> None:
    # The hygiene gate (ADR 0102): the one committed plan-eval input must stay loadable against the
    # CURRENT schema — a frozen fixture must not rot silently at the eval's front door.
    from ruamel.yaml import YAML

    from cyberlab_gen.schemas.attack_spec import CURRENT_ATTACK_SPEC_VERSION, AttackSpec
    from cyberlab_gen.schemas.loading import load_spec

    path = attack_spec_path(load_manifest().entry("aws-codebuild-actor-id-regex-bypass"))
    assert path is not None
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    spec = load_spec(data)
    assert isinstance(spec, AttackSpec)
    assert spec.spec_version == CURRENT_ATTACK_SPEC_VERSION
