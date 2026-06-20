"""Tests for the Extractor tool executor (``agents.md §5.4``, ADR 0021).

Covers: external_lookup records the trace and NVD results; propose_value_type and
target/lab_class_signal facet proposals are collected; a runtime:* facet proposal
is rejected at the tool boundary (the Extractor is not its authority); unknown
source ids and unknown tools return error results without raising.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor.tools import (
    TOOL_EXTERNAL_LOOKUP,
    TOOL_PROPOSE_FACET,
    TOOL_PROPOSE_THESIS_TYPE,
    TOOL_PROPOSE_VALUE_TYPE,
    ExtractorToolExecutor,
    extractor_tool_definitions,
)
from cyberlab_gen.errors import ExternalApiRateLimitError
from cyberlab_gen.framework.enrichment import NvdCveData
from cyberlab_gen.providers.base import ToolCall
from cyberlab_gen.registries.merge import load_merged_registries

if TYPE_CHECKING:
    import pytest


class _FakeNvd:
    def __init__(self, known: dict[str, NvdCveData]) -> None:
        self._known = known

    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        return self._known.get(cve_id)


def _registries() -> object:
    return load_merged_registries()


def _executor(nvd: _FakeNvd | None = None) -> ExtractorToolExecutor:
    return ExtractorToolExecutor(registries=_registries(), nvd_client=nvd)  # type: ignore[arg-type]


def _call(tool: str, args: dict[str, object]) -> ToolCall:
    return ToolCall(call_id="c1", tool_name=tool, arguments=args)


async def test_external_lookup_records_nvd_hit() -> None:
    nvd = _FakeNvd({"CVE-2024-0001": NvdCveData(cve_id="CVE-2024-0001", cvss_score=7.5)})
    ex = _executor(nvd)
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {"cve_id": "CVE-2024-0001"}})
    )
    assert not result.is_error
    assert len(ex.lookups) == 1
    assert ex.lookups[0].found is True
    assert ex.lookups[0].source_id == "nvd"


async def test_external_lookup_records_nvd_miss() -> None:
    ex = _executor(_FakeNvd({}))
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {"cve_id": "CVE-9999-9999"}})
    )
    assert not result.is_error
    assert ex.lookups[0].found is False


class _RateLimitedNvd:
    def lookup_cve(self, cve_id: str) -> NvdCveData | None:
        raise ExternalApiRateLimitError(f"nvd rate-limited for {cve_id}")


async def test_external_lookup_rate_limit_is_recorded_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Graceful degradation (pipeline.md §3.2.4): a rate-limit is recorded as a
    # skipped lookup and the run continues — but it must NOT be silently swallowed.
    ex = _executor(_RateLimitedNvd())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        result = await ex.execute(
            _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {"cve_id": "CVE-2024-0001"}})
        )
    assert not result.is_error  # recorded as not-found, not a hard tool error
    assert ex.lookups[0].found is False
    assert any(
        "rate-limited" in r.getMessage().lower() and "CVE-2024-0001" in r.getMessage()
        for r in caplog.records
    )


async def test_external_lookup_unavailable_source_is_graceful_not_fatal() -> None:
    # An unavailable source (unknown id, or registered-but-unwired) must NOT be an
    # error result: the provider turns is_error into a pydantic-ai ModelRetry, and
    # retrying an unservable lookup exhausts the tool-retry budget and kills the whole
    # extraction (ADR 0042). It is recorded as a not-found lookup so the run continues.
    ex = _executor()
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "mitre", "params": {"technique_id": "T1078"}})
    )
    assert not result.is_error
    assert "unavailable" in result.content
    assert len(ex.lookups) == 1
    assert ex.lookups[0].source_id == "mitre"
    assert ex.lookups[0].found is False


async def test_nvd_lookup_missing_cve_id_is_graceful_not_fatal() -> None:
    # A fumbled/missing cve_id param must NOT be a fatal tool error (ADR 0042): the provider
    # turns is_error into a pydantic-ai ModelRetry, and a missing param can't be fixed by
    # retrying the same call, so it would exhaust the tool-retry budget and kill the whole
    # extraction (the Wiz run hit exactly this). It is recorded as a not-found lookup, with
    # the model steered to mark the field unknown, so the loop continues. Proven WITH an NVD
    # client wired so it does not rely on the no-client degrade path.
    ex = _executor(_FakeNvd({"CVE-2024-0001": NvdCveData(cve_id="CVE-2024-0001", cvss_score=7.5)}))
    result = await ex.execute(_call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {}}))

    assert not result.is_error
    assert len(ex.lookups) == 1
    assert ex.lookups[0].source_id == "nvd"
    assert ex.lookups[0].found is False
    # steers the model to mark the field unknown rather than re-fire a doomed lookup
    content = result.content.lower()
    assert "external research" in content or "unknown_from_blog" in content


async def test_nvd_lookup_blank_cve_id_is_graceful_not_fatal() -> None:
    # A present-but-blank cve_id (whitespace) is the same fumble — graceful, not fatal.
    ex = _executor(_FakeNvd({}))
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {"cve_id": "   "}})
    )
    assert not result.is_error
    assert ex.lookups[0].found is False


async def test_propose_value_type_collected() -> None:
    ex = _executor()
    result = await ex.execute(
        _call(
            TOOL_PROPOSE_VALUE_TYPE,
            {
                "name": "k8s_sa_token",
                "description": "Kubernetes service account JWT token",
                "value_schema": {"type": "string"},
                "sensitive": True,
                "reasoning": "blog harvests JWT tokens from /var/run/secrets",
            },
        )
    )
    assert not result.is_error
    assert len(ex.value_type_proposals) == 1
    assert ex.value_type_proposals[0].name == "k8s_sa_token"


async def test_propose_target_facet_collected() -> None:
    ex = _executor()
    result = await ex.execute(
        _call(
            TOOL_PROPOSE_FACET,
            {
                "name": "target:eks",
                "category": "target",
                "description": "targets an EKS cluster",
                "applies_at_levels": ["lab"],
                "reasoning": "blog attacks an EKS control plane",
            },
        )
    )
    assert not result.is_error
    assert len(ex.facet_proposals) == 1
    assert ex.facet_proposals[0].category == "target"


def test_tool_definitions_advertise_four_tools_with_registered_sources() -> None:
    defs = extractor_tool_definitions(registered_source_ids=["nvd"])
    names = {d.name for d in defs}
    assert names == {
        TOOL_EXTERNAL_LOOKUP,
        TOOL_PROPOSE_VALUE_TYPE,
        TOOL_PROPOSE_FACET,
        TOOL_PROPOSE_THESIS_TYPE,
    }
    lookup = next(d for d in defs if d.name == TOOL_EXTERNAL_LOOKUP)
    # The description names the registered source and steers away from a 'mitre' source.
    assert "'nvd'" in lookup.description
    assert "mitre" in lookup.description.lower()


async def test_propose_thesis_type_collected() -> None:
    ex = _executor()
    result = await ex.execute(
        _call(
            TOOL_PROPOSE_THESIS_TYPE,
            {
                "name": "ci_cd_compromise",
                "description": "Compromise of a CI/CD build pipeline.",
                "reasoning": "blog's thesis is a CI/CD pipeline takeover, no matching type",
            },
        )
    )
    assert not result.is_error
    assert len(ex.thesis_type_proposals) == 1
    assert ex.thesis_type_proposals[0].name == "ci_cd_compromise"


async def test_propose_runtime_facet_rejected_at_boundary() -> None:
    # The Extractor is NOT the authority for runtime:* facets (schema.md §4.16). The
    # proposal is still dropped (not recorded), but the rejection must NOT be an error
    # result: a proposal is an optional side-channel, and an out-of-authority category
    # can never be fixed by retrying — as a ModelRetry (budget 1) it would escalate to a
    # fatal ToolRetryError over an optional proposal (ADR 0043).
    ex = _executor()
    result = await ex.execute(
        _call(
            TOOL_PROPOSE_FACET,
            {
                "name": "runtime:aws_lambda",
                "category": "runtime",
                "description": "runs in lambda",
                "applies_at_levels": ["phase"],
                "reasoning": "lambda execution",
            },
        )
    )
    assert not result.is_error  # rejected, but never fatal
    assert "not recorded" in result.content
    assert "Planner" in result.content
    assert ex.facet_proposals == []


async def test_propose_invalid_value_type_is_dropped_not_fatal() -> None:
    # A malformed proposal is dropped with an explanation, never an error result (ADR 0043).
    ex = _executor()
    result = await ex.execute(
        _call(TOOL_PROPOSE_VALUE_TYPE, {"name": "x"})  # missing required description/reasoning
    )
    assert not result.is_error
    assert "rejected" in result.content
    assert ex.value_type_proposals == []


async def test_unknown_tool_is_error_not_raise() -> None:
    ex = _executor()
    result = await ex.execute(_call("not_a_tool", {}))
    assert result.is_error


# --- caller-aware unavailable-source reply (ADR 0105) ----------------------


def _verify_executor(nvd: _FakeNvd | None = None) -> ExtractorToolExecutor:
    return ExtractorToolExecutor(registries=_registries(), nvd_client=nvd, verify_only=True)  # type: ignore[arg-type]


async def test_verify_only_unavailable_source_steers_to_verdict_not_other_sources() -> None:
    # ADR 0105: a verify-only reviewer (the juries) cannot set fields and must not keep trying other
    # sources — that spirals the whole source catalog into a ToolLoopError (run-20260620). The
    # unavailable reply steers it to emit its verdict, and must NOT carry producer-only guidance.
    ex = _verify_executor()
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "cisa_kev", "params": {"keyword": "x"}})
    )
    assert not result.is_error
    content = result.content.lower()
    assert "proceed to your verdict" in content
    assert "do not try other" in content
    assert "unknown_from_blog" not in content  # producer-only steer must not leak to a reviewer


async def test_verify_only_nvd_no_client_steers_to_verdict() -> None:
    # The nvd no-client degrade path is also caller-aware: a verify-only reviewer is steered to its
    # verdict, not to "record as requires external research" (which it cannot do).
    ex = _verify_executor()  # no nvd client
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "nvd", "params": {"cve_id": "CVE-2024-0001"}})
    )
    assert not result.is_error
    assert "proceed to your verdict" in result.content.lower()


async def test_producer_unavailable_source_keeps_unknown_from_blog_guidance() -> None:
    # The producer path is unchanged (ADR 0042 needs the "mark unknown and continue" steer so the
    # Extractor/Planner record the gap and proceed, never a fatal error result).
    ex = _executor()  # verify_only=False
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "cisa_kev", "params": {"keyword": "x"}})
    )
    content = result.content.lower()
    assert "unknown_from_blog" in content
    assert "proceed to your verdict" not in content


# --- verify-only external_lookup gate (ADR 0105) ---------------------------


def test_verify_only_with_no_work_advertises_no_tools() -> None:
    # ADR 0105: a verify-only agent with nothing a live source can check is handed NO tool — not even
    # the dead external_lookup — so it cannot spiral on it.
    assert extractor_tool_definitions(["nvd"], verify_only=True, offer_external_lookup=False) == []


def test_verify_only_with_work_advertises_only_external_lookup() -> None:
    defs = extractor_tool_definitions(["nvd"], verify_only=True, offer_external_lookup=True)
    assert {d.name for d in defs} == {TOOL_EXTERNAL_LOOKUP}


def test_producer_keeps_full_inventory_regardless_of_offer_flag() -> None:
    # The gate is verify-only-only: a producer always keeps external_lookup + its propose_* tools.
    defs = extractor_tool_definitions(["nvd"], offer_external_lookup=False)
    assert {d.name for d in defs} == {
        TOOL_EXTERNAL_LOOKUP,
        TOOL_PROPOSE_VALUE_TYPE,
        TOOL_PROPOSE_FACET,
        TOOL_PROPOSE_THESIS_TYPE,
    }
