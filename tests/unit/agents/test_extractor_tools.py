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
    TOOL_PROPOSE_VALUE_TYPE,
    ExtractorToolExecutor,
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


async def test_external_lookup_unknown_source_is_error() -> None:
    ex = _executor()
    result = await ex.execute(
        _call(TOOL_EXTERNAL_LOOKUP, {"source_id": "not_a_source", "params": {}})
    )
    assert result.is_error
    assert ex.lookups == []


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


async def test_propose_runtime_facet_rejected_at_boundary() -> None:
    # The Extractor is NOT the authority for runtime:* facets (schema.md §4.16).
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
    assert result.is_error
    assert "Planner" in result.content
    assert ex.facet_proposals == []


async def test_unknown_tool_is_error_not_raise() -> None:
    ex = _executor()
    result = await ex.execute(_call("not_a_tool", {}))
    assert result.is_error
