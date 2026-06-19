"""Tests for the blog-set manifest loader (ADR 0014 shape, ADR 0025 loader).

Covers the happy path (the bundled manifest loads), the forward-compat invariant
(every ``walk:`` path resolves — ADR 0014), and loader discipline on missing /
malformed files (``CLAUDE.md`` loader rules).
"""

from __future__ import annotations

import re
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
    assert (
        "vulnerability_disclosure" in tags
    )  # substantive vulnerability_story (walk §14 spelling; ADR 0103)
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


# --- manifest <-> walk coverage-tag consistency (ADR 0103) -------------------


def _coverage_tags_in_walk_section_14(walk_text: str) -> set[str]:
    """Extract the coverage tags *declared* in a walk's section 14.

    The subset rule (ADR 0103): every manifest ``coverage_tags`` entry must appear
    verbatim in its walk's §14. Walks write §14 either as a bullet list or an inline
    comma list, sometimes followed by negated / "not applied" commentary. To avoid
    counting a *negated* mention ("No ``multi_platform``") or a parenthetical aside
    ("see ``eval.md §7.3``") as a declared tag, this drops parentheticals and any
    "Not applied"/"Not applicable" tail, then ignores tokens immediately preceded by
    a negation word. The remaining backtick-quoted tag-grammar tokens are the
    declared set. (Erring toward a *larger* declared set is safe-ish; this keeps the
    common false-pass holes — negations and asides — closed.)
    """
    section_match = re.search(r"^## 14\b.*?(?=^## 15\b)", walk_text, re.DOTALL | re.MULTILINE)
    section = section_match.group(0) if section_match is not None else ""
    section = re.sub(r"\([^)]*\)", " ", section)  # drop parenthetical asides
    section = re.split(r"(?i)not appli(?:ed|cable)", section)[0]  # drop a "not applied" tail
    tags: set[str] = set()
    for token in re.finditer(r"`([a-z][a-z0-9_]*(?::[\w.\-]+)?)`", section):
        preceding = section[max(0, token.start() - 6) : token.start()].lower()
        if re.search(r"\bno[t]?\s*$", preceding):
            continue  # skip a negated mention ("no `x`" / "not `x`")
        tags.add(token.group(1))
    return tags


def test_manifest_coverage_tags_subset_of_walk_section_14() -> None:
    # ADR 0103: the manifest's per-entry coverage_tags are a disciplined *index* — a verbatim
    # subset of the walk's §14, not a free reword. Enforced so the two can't silently drift again
    # (e.g. the old manifest `target:gke` vs walk `platform:gke`, or `vulnerability_disclosure:present`
    # vs the walks' bare `vulnerability_disclosure`).
    manifest = load_manifest()
    offenders: dict[str, list[str]] = {}
    for entry in manifest.curated:
        declared = _coverage_tags_in_walk_section_14(walk_path(entry).read_text(encoding="utf-8"))
        extra = sorted(set(entry.coverage_tags) - declared)
        if extra:
            offenders[entry.id] = extra
    assert not offenders, (
        "manifest coverage_tags must appear verbatim in the walk's §14 — offenders: "
        + "; ".join(f"{blog_id}: {tags}" for blog_id, tags in sorted(offenders.items()))
    )


def test_manifest_shape_field_matches_walk_section_1() -> None:
    # ADR 0103: one `shape` value per entry, identical in the walk §1 header and the manifest.
    manifest = load_manifest()
    mismatches: dict[str, tuple[str, str | None]] = {}
    for entry in manifest.curated:
        walk_text = walk_path(entry).read_text(encoding="utf-8")
        m = re.search(r"^- \*\*shape:\*\*\s*`([a-z_]+)`", walk_text, re.MULTILINE)
        walk_shape = m.group(1) if m is not None else None
        if walk_shape != entry.shape:
            mismatches[entry.id] = (entry.shape, walk_shape)
    assert not mismatches, (
        "manifest `shape` must match the walk §1 header — mismatches (manifest, walk): "
        + "; ".join(f"{blog_id}: {pair}" for blog_id, pair in sorted(mismatches.items()))
    )
