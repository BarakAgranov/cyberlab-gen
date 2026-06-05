"""Tests for the per-blog eval runner (``run_blog_set``, ADR 0025).

Asserts the N-runs-per-blog invocation pattern (``eval.md §7.6``), that the
report carries per-blog aggregates + flat records, and that ``record_from_run``
maps a finished run's parts onto the metrics correctly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from eval.runner.manifest import load_manifest
from eval.runner.runner import ProviderBackedEvalRunner, run_blog_set
from tests.eval.conftest import FakeEvalRunner, make_record, make_spec

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.cli.extract import RunResult
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.attack_spec import AttackSpec


def test_run_blog_set_invokes_each_curated_blog_n_times() -> None:
    manifest = load_manifest()
    runner = FakeEvalRunner()
    report = run_blog_set(manifest=manifest, runner=runner, n=3, provider_backed=False)

    curated_ids = [e.id for e in manifest.curated]
    # every curated blog ran exactly N=3 times, indices 0..2
    for blog_id in curated_ids:
        assert sorted(i for (b, i) in runner.calls if b == blog_id) == [0, 1, 2]
    assert len(report.records) == 3 * len(curated_ids)
    assert len(report.aggregates) == len(curated_ids)
    assert report.runs_per_blog == 3
    assert report.provider_backed is False
    assert report.rotation_generation == manifest.rotation_generation


class _FakeExtractRunner:
    """A scripted ``ExtractRunner`` that ships a fixed spec without a provider."""

    def __init__(self, spec: AttackSpec) -> None:
        self._spec = spec

    def run(self, url: str, *, ledger: CostLedger) -> RunResult:
        from cyberlab_gen.cli.extract import RunResult

        del url, ledger
        return RunResult(spec=self._spec)

    def re_run_with_feedback(  # pragma: no cover
        self, feedback: str, *, ledger: CostLedger
    ) -> RunResult:
        raise NotImplementedError


class _PassValidator:
    """A duck-typed validator whose Layer-1 result always passes."""

    def validate(self, spec: object) -> object:
        from types import SimpleNamespace

        del spec
        return SimpleNamespace(passed=True)  # shipped path reads only `.passed`


def test_provider_backed_runner_writes_shipped_spec_to_disk(tmp_path: Path) -> None:
    # The report records only metrics; the shipped AttackSpec itself must be written
    # so a maintainer can read it (e.g. to check a `completeness=0.85` ship wasn't a
    # truncated emit). Written as <blog_id>-run<run_index>.yaml under specs_dir.
    from cyberlab_gen.cli.extract import _load_spec_from_yaml  # pyright: ignore[reportPrivateUsage]

    spec = make_spec(completeness=0.85)
    specs_dir = tmp_path / "specs"
    runner = ProviderBackedEvalRunner(
        extract_runner_factory=lambda _ledger: _FakeExtractRunner(spec),
        validator=_PassValidator(),  # type: ignore[arg-type]
        url_for=lambda _blog_id: "https://example.com/blog",
        specs_dir=specs_dir,
    )

    record = runner.run_once("ai-assisted-aws-intrusion", run_index=2)

    assert record.shipped
    path = specs_dir / "ai-assisted-aws-intrusion-run2.yaml"
    assert path.is_file()
    # the written YAML round-trips back to the same spec (byte-faithful, not lossy)
    reloaded = _load_spec_from_yaml(path.read_text(encoding="utf-8"))
    assert reloaded == spec


def test_provider_backed_runner_writes_no_spec_when_specs_dir_unset(tmp_path: Path) -> None:
    # Default (no specs_dir): nothing is written, and a shipped run still records.
    spec = make_spec()
    runner = ProviderBackedEvalRunner(
        extract_runner_factory=lambda _ledger: _FakeExtractRunner(spec),
        validator=_PassValidator(),  # type: ignore[arg-type]
        url_for=lambda _blog_id: "https://example.com/blog",
    )
    record = runner.run_once("b", run_index=0)
    assert record.shipped
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere under tmp


def test_run_blog_set_rejects_zero_n() -> None:
    manifest = load_manifest()
    with pytest.raises(ValueError, match="must be >= 1"):
        run_blog_set(manifest=manifest, runner=FakeEvalRunner(), n=0, provider_backed=False)


def test_run_blog_set_honors_explicit_blog_ids() -> None:
    manifest = load_manifest()
    target = manifest.curated[0].id
    report = run_blog_set(
        manifest=manifest,
        runner=FakeEvalRunner(),
        n=2,
        provider_backed=False,
        blog_ids=[target],
    )
    assert report.blog_ids == [target]
    assert len(report.records) == 2


def test_scripted_records_flow_through_aggregates() -> None:
    manifest = load_manifest()
    target = manifest.curated[0].id
    scripted = {
        target: [
            make_record(target, 0, layer1_passed=True),
            make_record(target, 1, layer1_passed=False),
        ]
    }
    runner = FakeEvalRunner(scripted)
    report = run_blog_set(
        manifest=manifest, runner=runner, n=2, provider_backed=False, blog_ids=[target]
    )
    agg = report.aggregates[0]
    assert agg.blog_id == target
    assert agg.layer1_pass_rate == 0.5
