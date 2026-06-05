"""Tests for the eval's spend guards: fail-fast + cost cap + real cost (ADR 0030).

A prior run spent ~$3.93 grinding through runs that were all systemically broken.
These cover the two protections that stop that, and the wrapper that makes the
cost cap act on *real* spend rather than the (previously hollow) ledger:

* **fail-fast** — abort after N consecutive *non-retryable* failures with the
  same (normalized) signature; a transient blip never aborts.
* **cost cap** — stop once cumulative spend reaches the ceiling.
* **CostRecordingProvider** — records each call's cost into the per-run ledger.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel

from eval.runner.cost_recording_provider import CostRecordingProvider
from eval.runner.manifest import load_manifest
from eval.runner.runner import (
    _failure_signature,  # pyright: ignore[reportPrivateUsage]
    _normalize_failure,  # pyright: ignore[reportPrivateUsage]
    run_blog_set,
)
from tests.eval.conftest import make_failure_record, make_record

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cyberlab_gen.providers.base import (
        AgentLabel,
        CapabilityHint,
        Message,
        ToolDefinition,
        ToolExecutor,
    )
    from eval.runner.metrics import BlogRunRecord


# --- fail-fast --------------------------------------------------------------


class _ScriptedRunner:
    """Returns whatever ``record_for(call_index, blog_id, run_index)`` yields."""

    def __init__(self, record_for: Callable[[int, str, int], BlogRunRecord]) -> None:
        self._record_for = record_for
        self.calls: list[tuple[str, int]] = []

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:
        idx = len(self.calls)
        self.calls.append((blog_id, run_index))
        return self._record_for(idx, blog_id, run_index)


def _curated_ids() -> list[str]:
    return [e.id for e in load_manifest().curated]


def test_fail_fast_aborts_on_repeated_identical_nonretryable_failure() -> None:
    # Every run fails with a 400 whose toolu id + message index VARY (like the real
    # one) but normalize to the same signature. With abort_after=2 the eval stops
    # after the 2nd consecutive failure; later blogs are skipped, not run.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        reason = (
            f"Anthropic call failed (400): messages.{idx + 5}: tool_use ids were found "
            f"without tool_result blocks immediately after: toolu_{idx}abc"
        )
        return make_failure_record(
            blog_id, run_index, failure_kind="non_retryable", halt_reason=reason
        )

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )

    assert len(runner.calls) == 2  # stopped after 2 runs, not 3 blogs x 3
    assert len(report.records) == 2
    # the two not-yet-run curated blogs are recorded skipped with the abort reason.
    skipped_ids = {s.blog_id for s in report.skipped}
    assert skipped_ids == set(_curated_ids()[1:])
    assert all("consecutive" in s.reason for s in report.skipped)


def test_transient_failures_do_not_abort() -> None:
    # A persistent transient failure is tagged retryable → it never aborts the eval.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        return make_failure_record(
            blog_id, run_index, failure_kind="retryable", halt_reason="timeout after 3 attempts"
        )

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )
    assert len(runner.calls) == 3 * len(_curated_ids())  # every run executed
    assert report.skipped == []


def test_distinct_nonretryable_failures_do_not_abort() -> None:
    # Two different non-retryable errors alternating never reach 2-in-a-row → no abort.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        reason = "error alpha" if idx % 2 == 0 else "error beta"
        return make_failure_record(
            blog_id, run_index, failure_kind="non_retryable", halt_reason=reason
        )

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )
    assert len(runner.calls) == 3 * len(_curated_ids())
    assert report.skipped == []


# --- cost cap ---------------------------------------------------------------


def test_cost_cap_aborts_and_archives_partial(tmp_path: Path) -> None:
    from eval.runner.cli import run_eval

    # Each (successful) run costs $3; the $5 cap is reached after the 2nd run.
    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        return make_record(blog_id, run_index, cost="3.00")

    runner = _ScriptedRunner(record_for)
    report, path = run_eval(
        runner=runner,
        provider_backed=False,
        n=3,
        reports_dir=tmp_path,
        cost_cap_usd=Decimal("5"),
    )

    assert len(runner.calls) == 2  # stopped before the 3rd run
    assert report.total_cost_usd() == Decimal("6.00")
    assert {s.blog_id for s in report.skipped} == set(_curated_ids()[1:])
    assert all("cost cap" in s.reason for s in report.skipped)
    # the partial report is on disk and reloads.
    from eval.runner.report import load_report

    assert path.is_file()
    assert len(load_report(path).records) == 2


def test_no_cost_cap_runs_everything() -> None:
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        return make_record(blog_id, run_index, cost="3.00")

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest, runner=runner, n=2, provider_backed=False, cost_cap_usd=None
    )
    assert len(runner.calls) == 2 * len(_curated_ids())
    assert report.skipped == []


# --- failure-signature normalization ----------------------------------------


def test_normalize_failure_collapses_varying_tool_ids_and_indices() -> None:
    a = (
        "Anthropic call failed (400): messages.7: tool_use ids were found without "
        "tool_result blocks immediately after: toolu_01ABC"
    )
    b = (
        "Anthropic call failed (400): messages.5: tool_use ids were found without "
        "tool_result blocks immediately after: toolu_99ZZZ"
    )
    assert _normalize_failure(a) == _normalize_failure(b)


def test_normalize_failure_collapses_varying_request_ids() -> None:
    # The real 400s differed ONLY by the alphanumeric request_id (req_...), which
    # the digit-only collapse left distinct, so fail-fast never tripped on six
    # identical failures (ADR 0032). request_ids must normalize equal.
    a = (
        "Anthropic call failed (400): messages.7: tool_use ids were found without "
        "tool_result blocks: toolu_01ABC, 'request_id': 'req_011Cbdq7ZqaTxP7if8SKGZSb'"
    )
    b = (
        "Anthropic call failed (400): messages.7: tool_use ids were found without "
        "tool_result blocks: toolu_99ZZZ, 'request_id': 'req_011CbdqF4oZFBM52LGdA1cvE'"
    )
    assert _normalize_failure(a) == _normalize_failure(b)


def test_fail_fast_aborts_on_repeated_400_differing_only_by_request_id() -> None:
    # End-to-end regression for the gen0-20260602 archive: six non-retryable 400s
    # that differed only by request_id ran in full because the signature never
    # matched. With request_id normalization, fail-fast stops after the 2nd.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        # request_id varies by a LETTER (chr) so it is NOT collapsed by the
        # digit-only rule — exactly the real failure mode.
        rid_letter = chr(ord("A") + idx)
        reason = (
            f"Anthropic call failed (400): messages.{idx + 5}: tool_use ids were found "
            f"without tool_result blocks: toolu_{idx}abc, "
            f"'request_id': 'req_011Cbdq{rid_letter}ZqaTxPif8SKGZSb'"
        )
        return make_failure_record(
            blog_id, run_index, failure_kind="non_retryable", halt_reason=reason
        )

    runner = _ScriptedRunner(record_for)
    run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )
    assert len(runner.calls) == 2  # aborted after 2, not all 6


def test_failure_signature_is_none_for_clean_and_retryable_runs() -> None:
    clean = make_record("b", 0)
    assert _failure_signature(clean) is None
    transient = make_failure_record("b", 0, failure_kind="retryable", halt_reason="timeout")
    assert _failure_signature(transient) is None
    hard = make_failure_record("b", 0, failure_kind="non_retryable", halt_reason="boom")
    assert _failure_signature(hard) is not None


# --- CostRecordingProvider --------------------------------------------------


class _Out(BaseModel):
    ok: bool = True


class _FakeInnerProvider:
    """A minimal ``Provider`` returning a fixed-cost response per call."""

    def __init__(self, cost: str) -> None:
        from cyberlab_gen.providers.base import TokenUsage

        self._usage = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=Decimal(cost),
        )
        self.complete_calls = 0
        self.tool_calls = 0

    @property
    def name(self) -> str:
        return "fake"

    def _response(self) -> object:
        from cyberlab_gen.providers.base import ProviderResponse

        return ProviderResponse[_Out](
            output=_Out(), raw_text="{}", usage=self._usage, model="m", provider="fake"
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> object:
        self.complete_calls += 1
        return self._response()

    async def complete_with_tools(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> object:
        self.tool_calls += 1
        return self._response()


async def test_cost_recording_provider_records_each_call_into_the_ledger() -> None:
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    inner = _FakeInnerProvider(cost="0.50")
    provider = CostRecordingProvider(inner, ledger)  # type: ignore[arg-type]

    assert ledger.total_usd == Decimal("0")
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert ledger.total_usd == Decimal("0.50")  # real cost flowed into the ledger
    await provider.complete_with_tools(
        [],
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        tools=[],
        tool_executor=_unused_executor(),
        agent_label=AgentLabel.EXTRACTOR,
        max_iterations=3,
    )
    assert ledger.total_usd == Decimal("1.00")  # accumulates across calls
    assert len(ledger.entries) == 2


def _unused_executor() -> ToolExecutor:
    from cyberlab_gen.providers.base import ToolCall, ToolResult

    class _E:
        async def execute(self, call: ToolCall) -> ToolResult:
            return ToolResult(call_id=call.call_id, content="unused")

    return _E()
