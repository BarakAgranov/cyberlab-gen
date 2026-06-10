"""Tests for the shared mechanical-validator finding/result contract (ADR 0073).

Pins that both Phase-1 validator layers expose the one ``Finding``/``FindingResult`` shape, and that
``render()`` / ``rendered_findings()`` live once in the base.
"""

from __future__ import annotations

from cyberlab_gen.validators.base import Finding, FindingResult
from cyberlab_gen.validators.grounding_validator import (
    GroundingCode,
    GroundingFinding,
    GroundingResult,
)
from cyberlab_gen.validators.static_schema_validator import (
    StaticSchemaCode,
    StaticSchemaFinding,
    StaticSchemaResult,
)


def test_both_layers_share_the_finding_result_base() -> None:
    assert issubclass(StaticSchemaFinding, Finding)
    assert issubclass(GroundingFinding, Finding)
    assert issubclass(StaticSchemaResult, FindingResult)
    assert issubclass(GroundingResult, FindingResult)


def test_render_and_rendered_findings_live_in_the_base() -> None:
    code = next(iter(StaticSchemaCode))
    f = StaticSchemaFinding(code=code, location="chain.chain_steps[0]", detail="bad")
    assert f.render() == f"{code.value}@chain.chain_steps[0]: bad"

    result = StaticSchemaResult(passed=False, findings=[f])
    assert result.rendered_findings() == [f.render()]


def test_grounding_result_keeps_its_retry_view_on_the_shared_base() -> None:
    g = GroundingFinding(
        code=GroundingCode.SEARCH_BEFORE_CLAIM, location="cves[0]", detail="unsupported"
    )
    result = GroundingResult(findings=[g])
    assert result.rendered_findings() == [g.render()]  # inherited from the base
    assert result.needs_retry  # layer-specific view still works
    assert result.retry_findings() == [g]
