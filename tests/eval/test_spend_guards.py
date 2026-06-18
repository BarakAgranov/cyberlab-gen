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

import pytest
from pydantic import BaseModel

from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
from eval.runner.manifest import load_manifest
from eval.runner.runner import (
    FAILURE_BLOG_FATAL,
    FAILURE_GLOBAL_FATAL,
    FAILURE_RETRYABLE,
    _classify_pipeline_failure,  # pyright: ignore[reportPrivateUsage]
    _failure_signature,  # pyright: ignore[reportPrivateUsage]
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


def test_blog_fatal_repeat_skips_blog_and_continues_to_next() -> None:
    # ADR 0034: a blog-specific failure that repeats identically stops THAT blog
    # (after abort_after=2 runs) but the run CONTINUES to the next blog — its
    # size/content problem says nothing about the others. The toolu id + index VARY
    # (like the real 400) but normalize to the same per-blog signature.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        reason = (
            f"Anthropic call failed (400): messages.{idx + 5}: request too large: toolu_{idx}abc"
        )
        return make_failure_record(
            blog_id, run_index, failure_kind=FAILURE_BLOG_FATAL, halt_reason=reason
        )

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )

    # Every curated blog is attempted; each stops after 2 identical runs (3rd skipped).
    assert len(runner.calls) == 2 * len(_curated_ids())
    # No blog is in `skipped` — each ran (partially) and has a record, not a skip.
    assert report.skipped == []
    assert {bid for bid, _ in runner.calls} == set(_curated_ids())


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


def test_distinct_blog_fatal_failures_do_not_stop_a_blog_early() -> None:
    # Two different blog-fatal errors alternating never reach 2-in-a-row within a
    # blog → no early stop, every run of every blog executes, nothing skipped.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        reason = "error alpha" if run_index % 2 == 0 else "error beta"
        return make_failure_record(
            blog_id, run_index, failure_kind=FAILURE_BLOG_FATAL, halt_reason=reason
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


def test_global_failure_aborts_the_whole_run() -> None:
    # ADR 0034: a global-fatal failure (auth/quota/no-served-model) aborts the whole
    # run on sight — the next blog would fail identically. The remaining blogs are
    # recorded `skipped`, not run.
    manifest = load_manifest()

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        return make_failure_record(
            blog_id,
            run_index,
            failure_kind=FAILURE_GLOBAL_FATAL,
            halt_reason="Anthropic call failed (401): authentication_error",
        )

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )
    # Aborted on the FIRST global failure — one call, not even the rest of blog 1.
    assert len(runner.calls) == 1
    assert {s.blog_id for s in report.skipped} == set(_curated_ids()[1:])
    assert all("global failure" in s.reason for s in report.skipped)


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
#
# The toolu_/req_ id collapses were scar tissue from the retired direct-Anthropic
# tool-loop adapter's "tool_use ids without tool_result" 400s; the pydantic-ai migration
# (ADR 0036) removed that adapter, so those id forms no longer appear and the collapses —
# and the tests that pinned them — were deleted (F1). The generic digit/message-index
# collapse that remains is still exercised by the blog-fatal fail-fast tests above.


def test_failure_signature_only_fires_for_blog_fatal_runs() -> None:
    clean = make_record("b", 0)
    assert _failure_signature(clean) is None
    transient = make_failure_record("b", 0, failure_kind=FAILURE_RETRYABLE, halt_reason="timeout")
    assert _failure_signature(transient) is None
    # Global-fatal aborts on sight (no counting), so it carries no signature.
    glob = make_failure_record("b", 0, failure_kind=FAILURE_GLOBAL_FATAL, halt_reason="401 auth")
    assert _failure_signature(glob) is None
    blog = make_failure_record("b", 0, failure_kind=FAILURE_BLOG_FATAL, halt_reason="truncated")
    assert _failure_signature(blog) is not None


def test_first_blog_fails_but_later_blogs_still_run() -> None:
    # The concrete scenario from the brief: the first blog truncates (blog-fatal) on
    # every run, but the next blog extracts fine. The first blog must NOT abort the
    # run — later blogs still get their turn (ADR 0034). Uses run_index for the
    # first blog so its failures are identical (-> stops early), records for the rest.
    manifest = load_manifest()
    curated = _curated_ids()
    first, rest = curated[0], curated[1:]

    def record_for(idx: int, blog_id: str, run_index: int) -> BlogRunRecord:
        if blog_id == first:
            return make_failure_record(
                blog_id,
                run_index,
                failure_kind=FAILURE_BLOG_FATAL,
                halt_reason="the AttackSpec emit was truncated at the 16384-token output limit",
            )
        return make_record(blog_id, run_index)  # later blogs succeed

    runner = _ScriptedRunner(record_for)
    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        abort_after_consecutive_failures=2,
    )
    # First blog stopped after 2 identical truncations; every later blog ran all 3.
    ran_by_blog = {bid: sum(1 for b, _ in runner.calls if b == bid) for bid in curated}
    assert ran_by_blog[first] == 2
    assert all(ran_by_blog[b] == 3 for b in rest)
    assert report.skipped == []  # nothing aborted; later blogs were not skipped
    assert set(report.blog_ids) == set(curated)  # all blogs have a (partial) aggregate


# --- failure classification (eval-runner triage) ----------------------------


def _status_error(code: int) -> Exception:
    class _StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    return _StatusError(code)


def test_classify_pipeline_failure_maps_each_error_to_its_scope() -> None:
    from cyberlab_gen.errors import (
        CapabilityUnreachable,
        EmitTruncated,
        HardFailure,
        MalformedOutput,
        TransientFailure,
        ValidationError,
    )

    # Retryable blip — never aborts/skips.
    assert _classify_pipeline_failure(TransientFailure("timeout")) == FAILURE_RETRYABLE
    # Global — the next blog fails identically.
    assert _classify_pipeline_failure(CapabilityUnreachable("no model")) == FAILURE_GLOBAL_FATAL
    assert (
        _classify_pipeline_failure(HardFailure("auth", cause=_status_error(401)))
        == FAILURE_GLOBAL_FATAL
    )
    assert (
        _classify_pipeline_failure(HardFailure("model gone", cause=_status_error(404)))
        == FAILURE_GLOBAL_FATAL
    )
    # No HTTP status (no API key / pricing / config) -> systemic -> global.
    assert _classify_pipeline_failure(HardFailure("no API key")) == FAILURE_GLOBAL_FATAL
    # Blog-specific — content/size of THIS blog.
    assert (
        _classify_pipeline_failure(HardFailure("too large", cause=_status_error(400)))
        == FAILURE_BLOG_FATAL
    )
    assert _classify_pipeline_failure(EmitTruncated("truncated")) == FAILURE_BLOG_FATAL
    assert _classify_pipeline_failure(MalformedOutput("bad spec")) == FAILURE_BLOG_FATAL
    assert (
        _classify_pipeline_failure(ValidationError("static schema validation halt"))
        == FAILURE_BLOG_FATAL
    )


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
        model: str,
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
        model: str,
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
        model="claude-test",
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert ledger.total_usd == Decimal("0.50")  # real cost flowed into the ledger
    await provider.complete_with_tools(
        [],
        output_schema=_Out,
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        model="claude-test",
        tools=[],
        tool_executor=_unused_executor(),
        agent_label=AgentLabel.EXTRACTOR,
        max_iterations=3,
    )
    assert ledger.total_usd == Decimal("1.00")  # accumulates across calls
    assert len(ledger.entries) == 2


class _RaisingInnerProvider:
    """A ``Provider`` whose ``complete_with_tools`` raises a billed ``ProviderError``.

    Models the accounting bug ADR 0033 fixes: a truncated/malformed emit that was
    billed by the vendor but ultimately raises. The provider attaches the billed
    usage + model to the error; the wrapper must record it even though no response
    comes back.
    """

    def __init__(self, *, billed_cost: str, attach_usage: bool = True) -> None:
        from cyberlab_gen.providers.base import TokenUsage

        self._usage = (
            TokenUsage(
                input_tokens=1000,
                output_tokens=4096,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=Decimal(billed_cost),
            )
            if attach_usage
            else None
        )

    @property
    def name(self) -> str:
        return "raising"

    async def complete(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        model: str,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> object:  # pragma: no cover - not exercised here
        raise AssertionError("unused")

    async def complete_with_tools(
        self,
        messages: list[Message],
        *,
        output_schema: type[_Out],
        capability: CapabilityHint,
        model: str,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> object:
        from cyberlab_gen.errors import EmitTruncated

        raise EmitTruncated(
            "the AttackSpec emit was truncated at the 16384-token output limit",
            usage=self._usage,
            model="claude-opus-4-8",
        )


async def test_cost_recording_provider_records_billed_usage_when_the_call_raises() -> None:
    # ADR 0033 accounting fix: a call that RAISES a ProviderError carrying billed
    # usage must still be recorded — otherwise the real cost exceeds the reported
    # cost and the cost cap goes blind. Recorded as a FAILED entry; total_usd counts it.
    from cyberlab_gen.errors import EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    inner = _RaisingInnerProvider(billed_cost="2.50")
    provider = CostRecordingProvider(inner, ledger)  # type: ignore[arg-type]

    with pytest.raises(EmitTruncated):
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )
    assert ledger.total_usd == Decimal("2.50")  # billed-but-raised spend was recorded
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.outcome is CallOutcome.FAILED
    assert entry.model == "claude-opus-4-8"
    assert entry.provider == "raising"


async def test_cost_recording_provider_logs_a_billed_failure_live(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Cost visibility (ADR 0038) must cover a call that RAISED, not only successes:
    # the Wiz-blog run died on a failed call and the per-call cost line is what makes
    # the spend visible in the run log as it happens. Assert the failed call logs too.
    import logging

    from cyberlab_gen.errors import EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(_RaisingInnerProvider(billed_cost="0.48"), ledger)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO), pytest.raises(EmitTruncated):
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )
    line = next((r.getMessage() for r in caplog.records if "LLM call #1" in r.getMessage()), "")
    assert "[failed]" in line
    assert "cost=$0.48" in line


async def test_cost_recording_provider_on_call_echoes_each_call() -> None:
    # --show-cost wiring (ADR 0038): an on_call sink receives a concise per-call line
    # for live display, in addition to the run-log INFO line.
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    seen: list[str] = []
    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(
        _FakeInnerProvider(cost="0.50"),  # type: ignore[arg-type]
        ledger,
        on_call=seen.append,
    )
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        model="claude-test",
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert len(seen) == 1
    assert "llm call #1" in seen[0]
    assert "cost=$0.50" in seen[0]


async def test_cost_recording_provider_skips_failure_with_no_billed_usage() -> None:
    # A ProviderError that carries no usage (failed before any vendor call billed)
    # records nothing — there is no honest cost to attribute.
    from cyberlab_gen.errors import EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    inner = _RaisingInnerProvider(billed_cost="0", attach_usage=False)
    provider = CostRecordingProvider(inner, ledger)  # type: ignore[arg-type]

    with pytest.raises(EmitTruncated):
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )
    assert ledger.entries == []  # nothing billed -> nothing recorded


async def test_cost_recording_provider_aborts_mid_run_at_catastrophe_ceiling() -> None:
    # ADR 0038: the framework-side wrapper raises BudgetExceeded once cumulative spend
    # crosses the ledger's cap (the high catastrophe ceiling) — the ledger never raises.
    from cyberlab_gen.errors import BudgetExceeded
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=Decimal("1.00"))
    inner = _FakeInnerProvider(cost="0.60")
    provider = CostRecordingProvider(inner, ledger)  # type: ignore[arg-type]

    # First call: $0.60, under the $1.00 ceiling — proceeds.
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        model="claude-test",
        agent_label=AgentLabel.EXTRACTOR,
    )
    # Second call: $1.20 cumulative crosses the ceiling — abort.
    with pytest.raises(BudgetExceeded) as exc_info:
        await provider.complete(
            [],
            output_schema=_Out,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            model="claude-test",
            agent_label=AgentLabel.EXTRACTOR,
        )
    assert ledger.total_usd == Decimal("1.20")  # the crossing call is still recorded
    assert len(ledger.entries) == 2
    assert exc_info.value.spent_usd == Decimal("1.20")
    assert exc_info.value.ceiling_usd == Decimal("1.00")
    assert exc_info.value.usage is not None  # billed usage attached for honest accounting
    assert exc_info.value.model == "m"


async def test_cost_recording_provider_trips_ceiling_on_billed_failures() -> None:
    # ADR 0038 amended by ADR 0047: a run made entirely of BILLED FAILURES (truncated/
    # malformed emits the vendor billed but that raise, ADR 0033) must trip the
    # catastrophe ceiling too. The ceiling used to be enforced only on the success path
    # (`_record`), on the false premise that a failed call's own error always halts the
    # run. It does not: a MalformedOutput is caught and RETRIED by the structural-retry
    # / refinement machinery, so billed spend accumulated with the ceiling never checked
    # and could overshoot the cap. The wrapper must bound a failing run like a succeeding
    # one.
    from cyberlab_gen.errors import BudgetExceeded, EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedger

    ledger = CostLedger(run_id="t", cap_usd=Decimal("1.00"))
    inner = _RaisingInnerProvider(billed_cost="0.60")
    provider = CostRecordingProvider(inner, ledger)  # type: ignore[arg-type]

    async def _failing_call() -> None:
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )

    # First billed failure: $0.60, under the $1.00 ceiling. Records the spend and
    # re-raises the ORIGINAL provider error unchanged — no escalation below the ceiling.
    with pytest.raises(EmitTruncated):
        await _failing_call()
    assert ledger.total_usd == Decimal("0.60")

    # Second billed failure: $1.20 cumulative crosses the ceiling. The wrapper now
    # escalates to BudgetExceeded (a HardFailure the retry machinery does not absorb) so
    # the runaway actually halts — instead of re-raising the original error, which the
    # structural-retry loop would have caught and retried.
    with pytest.raises(BudgetExceeded) as exc_info:
        await _failing_call()

    assert ledger.total_usd == Decimal("1.20")  # the crossing call's spend is recorded
    assert len(ledger.entries) == 2
    assert all(e.outcome is CallOutcome.FAILED for e in ledger.entries)  # all billed failures
    assert exc_info.value.spent_usd == Decimal("1.20")
    assert exc_info.value.ceiling_usd == Decimal("1.00")
    assert exc_info.value.usage is not None  # billed usage attached for honest accounting
    assert exc_info.value.model == "claude-opus-4-8"
    # The original billed failure is preserved as the cause, never masked.
    assert isinstance(exc_info.value.__cause__, EmitTruncated)
    assert isinstance(exc_info.value.cause, EmitTruncated)


async def test_cost_recording_provider_logs_each_call_with_cumulative(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ADR 0038 cost visibility: every billed call logs cost + running cumulative.
    import logging

    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(_FakeInnerProvider(cost="0.50"), ledger)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        await provider.complete(
            [],
            output_schema=_Out,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            model="claude-test",
            agent_label=AgentLabel.EXTRACTOR,
        )
    line = next((r.getMessage() for r in caplog.records if "LLM call #1" in r.getMessage()), "")
    assert "cost=$0.50" in line
    assert "cumulative=$0.50" in line


# --- CostRecordingProvider: trajectory sink (Item 1, ADR 0098) --------------


class _SpySink:
    """Records what the provider notified it of, to assert the chokepoint wiring."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []
        self.failures: list[tuple[object, object, object, object]] = []

    def record_call(self, response: object, *, agent_label: object, capability: object) -> None:
        self.calls.append((response, agent_label, capability))

    def record_failed_call(
        self, *, model: object, usage: object, agent_label: object, capability: object
    ) -> None:
        self.failures.append((model, usage, agent_label, capability))


async def test_cost_recording_provider_notifies_trajectory_sink_on_success() -> None:
    # The chokepoint hands the full ProviderResponse (with structured output) + agent identity
    # to the trajectory sink on every successful billed call — the content the run dir keeps.
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    sink = _SpySink()
    provider = CostRecordingProvider(
        _FakeInnerProvider(cost="0.50"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=sink,  # type: ignore[arg-type]
    )
    await provider.complete(
        [],
        output_schema=_Out,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        model="claude-test",
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert len(sink.calls) == 1
    response, agent_label, capability = sink.calls[0]
    assert agent_label is AgentLabel.EXTRACTOR
    assert capability is CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT
    # the structured output reached the sink (response is typed `object` on the spy)
    assert response.output == _Out()  # pyright: ignore[reportAttributeAccessIssue]


async def test_cost_recording_provider_notifies_trajectory_sink_on_billed_failure() -> None:
    # A billed-but-raised call has no response, only usage+model. The sink is told via the
    # failure path so the trajectory still records the FAILED round (metadata-only).
    from cyberlab_gen.errors import EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    sink = _SpySink()
    provider = CostRecordingProvider(
        _RaisingInnerProvider(billed_cost="2.50"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=sink,  # type: ignore[arg-type]
    )
    with pytest.raises(EmitTruncated):
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )
    assert sink.calls == []
    assert len(sink.failures) == 1
    model, _usage, agent_label, capability = sink.failures[0]
    assert model == "claude-opus-4-8"
    assert agent_label is AgentLabel.EXTRACTOR
    assert capability is CapabilityHint.LONG_CONTEXT_EXTRACTION


async def test_cost_recording_provider_captures_trajectory_before_ceiling_abort() -> None:
    # The catastrophe-ceiling abort must not swallow the very call that crossed it: capture
    # happens before _enforce_ceiling raises, so the run dir records the round that blew the cap.
    from cyberlab_gen.errors import BudgetExceeded
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=Decimal("1.00"))
    sink = _SpySink()
    provider = CostRecordingProvider(
        _FakeInnerProvider(cost="1.50"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=sink,  # type: ignore[arg-type]
    )
    with pytest.raises(BudgetExceeded):
        await provider.complete(
            [],
            output_schema=_Out,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            model="claude-test",
            agent_label=AgentLabel.EXTRACTOR,
        )
    assert len(sink.calls) == 1  # the crossing call was captured before the abort raised


class _RaisingSink:
    """A trajectory sink that always raises — models a capture bug (serialization / a future record
    field). It must never crash the run, mask the propagating error, or skip the catastrophe ceiling.
    """

    def record_call(self, response: object, *, agent_label: object, capability: object) -> None:
        raise RuntimeError("boom in trajectory capture")

    def record_failed_call(
        self, *, model: object, usage: object, agent_label: object, capability: object
    ) -> None:
        raise RuntimeError("boom in trajectory capture")


async def test_trajectory_sink_exception_does_not_perturb_a_successful_call() -> None:
    # ADR 0039 best-effort: a capture failure on the success path must be swallowed — the (already
    # billed) call returns normally, never crashing the run.
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(
        _FakeInnerProvider(cost="0.50"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=_RaisingSink(),  # type: ignore[arg-type]
    )
    await provider.complete(  # must NOT raise
        [],
        output_schema=_Out,
        capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
        model="claude-test",
        agent_label=AgentLabel.EXTRACTOR,
    )
    assert ledger.total_usd == Decimal("0.50")  # the call was still billed/recorded


async def test_trajectory_sink_exception_does_not_skip_the_ceiling_on_success() -> None:
    # §1.6 mechanical safety: a sink that raises must NOT bypass the catastrophe ceiling. The ceiling
    # fires for the cap-crossing call even though capture (which runs just before it) raised.
    from cyberlab_gen.errors import BudgetExceeded
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=Decimal("1.00"))
    provider = CostRecordingProvider(
        _FakeInnerProvider(cost="1.50"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=_RaisingSink(),  # type: ignore[arg-type]
    )
    with pytest.raises(BudgetExceeded):
        await provider.complete(
            [],
            output_schema=_Out,
            capability=CapabilityHint.FAST_CHEAP_STRUCTURED_OUTPUT,
            model="claude-test",
            agent_label=AgentLabel.EXTRACTOR,
        )


async def test_trajectory_sink_exception_does_not_mask_the_provider_error() -> None:
    # ADR 0039 "never mask the propagating error": on a billed failure, a sink that raises must not
    # replace the original ProviderError — the caller still sees the real failure.
    from cyberlab_gen.errors import EmitTruncated
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=None)
    provider = CostRecordingProvider(
        _RaisingInnerProvider(billed_cost="0.48"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=_RaisingSink(),  # type: ignore[arg-type]
    )
    with pytest.raises(EmitTruncated):  # the ORIGINAL error, not the sink's RuntimeError
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )


async def test_trajectory_sink_records_failed_round_before_ceiling_abort() -> None:
    # The FAILED-path twin of capture-before-ceiling: a billed failure that crosses the ceiling still
    # reaches the sink (record_failed_call) before BudgetExceeded raises (regression guard for the
    # _record_billed_failure ordering — ADR 0047 failure-dominated runs).
    from cyberlab_gen.errors import BudgetExceeded
    from cyberlab_gen.providers.base import AgentLabel, CapabilityHint
    from cyberlab_gen.providers.cost_ledger import CostLedger

    ledger = CostLedger(run_id="t", cap_usd=Decimal("1.00"))
    sink = _SpySink()
    provider = CostRecordingProvider(
        _RaisingInnerProvider(billed_cost="1.20"),  # type: ignore[arg-type]
        ledger,
        trajectory_sink=sink,  # type: ignore[arg-type]
    )
    with pytest.raises(BudgetExceeded):
        await provider.complete_with_tools(
            [],
            output_schema=_Out,
            capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
            model="claude-test",
            tools=[],
            tool_executor=_unused_executor(),
            agent_label=AgentLabel.EXTRACTOR,
            max_iterations=3,
        )
    assert len(sink.failures) == 1  # the cap-blowing FAILED round was captured before the abort


def _unused_executor() -> ToolExecutor:
    from cyberlab_gen.providers.base import ToolCall, ToolResult

    class _E:
        async def execute(self, call: ToolCall) -> ToolResult:
            return ToolResult(call_id=call.call_id, content="unused")

    return _E()
