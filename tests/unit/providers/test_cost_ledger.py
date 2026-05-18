"""Unit tests for the cost ledger and pricing table.

Pins from the Phase 0 Task 5b brief:

- Bundled ``pricing.yaml`` loads cleanly and the model rows we ship
  validate as ``ModelPricing``.
- ``compute_cost`` applies per-million-token math and uses the
  ``cache_write_5min`` rate for ``cache_write_tokens`` (Phase-0
  decision recorded in the execution log).
- ``CostLedger`` accumulates **per-attempt** entries — a logical call
  succeeding on retry 2 produces three entries (two ``FAILED``, one
  ``SUCCESS``) summing to the full billed cost (brief F4).
- ``cap_usd`` is data only: ``remaining_under_cap()`` returns ``None``
  when uncapped, a positive ``Decimal`` when under, and a negative
  ``Decimal`` after overrun — **no exception is raised**. The framework,
  not the provider, owns the budget decision (provider-interface.md
  §5.3 / brief F1).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cyberlab_gen.providers import (
    AgentLabel,
    CallOutcome,
    CapabilityHint,
    CostLedger,
    CostLedgerEntry,
    CostReportBlock,
    ModelPricing,
    PricingTable,
    TokenUsage,
    compute_cost,
    load_pricing_table,
)

_TS = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _entry(
    *,
    cost: Decimal,
    agent: AgentLabel = AgentLabel.PLANNER,
    provider: str = "anthropic",
    model: str = "claude-opus-4-7",
    capability: CapabilityHint = CapabilityHint.HIGH_QUALITY_REASONING,
    outcome: CallOutcome = CallOutcome.SUCCESS,
    purpose: str = "test",
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CostLedgerEntry:
    return CostLedgerEntry(
        timestamp=_TS,
        agent_label=agent,
        provider=provider,
        model=model,
        capability=capability,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        ),
        outcome=outcome,
        purpose=purpose,
    )


def test_pricing_table_loads_bundled() -> None:
    table = load_pricing_table()
    opus = table.lookup("anthropic", "claude-opus-4-7")
    assert isinstance(opus, ModelPricing)
    assert opus.input == Decimal("5.00")
    assert opus.output == Decimal("25.00")
    assert opus.cache_read == Decimal("0.50")
    assert opus.cache_write_5min == Decimal("6.25")
    assert opus.cache_write_1h == Decimal("10.00")


def test_pricing_table_lookup_unknown_provider_raises() -> None:
    table = load_pricing_table()
    with pytest.raises(KeyError, match="provider='openai'"):
        table.lookup("openai", "gpt-5")


def test_pricing_table_lookup_unknown_model_raises() -> None:
    table = load_pricing_table()
    with pytest.raises(KeyError, match="model='claude-opus-99'"):
        table.lookup("anthropic", "claude-opus-99")


def test_pricing_table_rejects_unknown_field() -> None:
    """``extra='forbid'`` on ``ModelPricing`` catches typos like ``inputt``.

    This is exactly the typo class that motivated using ``ArtifactModel``
    instead of ``InternalModel`` for the loaded YAML schemas — recorded
    in the plan as the user's pushback after the first draft.
    """
    with pytest.raises(ValidationError):
        PricingTable.model_validate(
            {
                "rows": {
                    "anthropic": {
                        "x": {
                            "inputt": "5.00",
                            "output": "25.00",
                            "cache_read": "0.50",
                            "cache_write_5min": "6.25",
                            "cache_write_1h": "10.00",
                        }
                    }
                }
            }
        )


def test_pricing_table_round_trip() -> None:
    original = load_pricing_table()
    dumped = original.model_dump()
    reloaded = PricingTable.model_validate(dumped)
    assert reloaded == original


def test_compute_cost_input_output_only() -> None:
    table = load_pricing_table()
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000, cost_usd=Decimal("0"))
    cost = compute_cost(table, provider="anthropic", model="claude-opus-4-7", usage=usage)
    # 1M * $5 + 0.5M * $25 = $5 + $12.50 = $17.50
    assert cost == Decimal("17.50")


def test_compute_cost_with_cache_read_and_write() -> None:
    table = load_pricing_table()
    usage = TokenUsage(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cost_usd=Decimal("0"),
    )
    cost = compute_cost(table, provider="anthropic", model="claude-opus-4-7", usage=usage)
    # Per Phase-0 decision: cache_write_tokens billed at 5-minute rate.
    # 1M * $0.50 (cache_read) + 1M * $6.25 (cache_write_5min) = $6.75
    assert cost == Decimal("6.75")


def test_compute_cost_zero_tokens_is_zero() -> None:
    table = load_pricing_table()
    usage = TokenUsage(input_tokens=0, output_tokens=0, cost_usd=Decimal("0"))
    cost = compute_cost(table, provider="anthropic", model="claude-opus-4-7", usage=usage)
    assert cost == Decimal("0")


def test_compute_cost_unknown_model_raises() -> None:
    table = load_pricing_table()
    usage = TokenUsage(input_tokens=10, output_tokens=10, cost_usd=Decimal("0"))
    with pytest.raises(KeyError):
        compute_cost(table, provider="anthropic", model="claude-nonexistent", usage=usage)


def test_cost_ledger_record_and_total() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("1.50")))
    ledger.record(_entry(cost=Decimal("2.25")))
    assert ledger.total_usd == Decimal("3.75")
    assert len(ledger.entries) == 2


def test_cost_ledger_by_agent_rollup() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("1.00"), agent=AgentLabel.PLANNER))
    ledger.record(_entry(cost=Decimal("0.50"), agent=AgentLabel.PLANNER))
    ledger.record(_entry(cost=Decimal("2.00"), agent=AgentLabel.CRITIC))
    rollup = ledger.by_agent()
    assert rollup == {
        AgentLabel.PLANNER: Decimal("1.50"),
        AgentLabel.CRITIC: Decimal("2.00"),
    }


def test_cost_ledger_by_model_rollup() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("1.00"), model="claude-opus-4-7"))
    ledger.record(_entry(cost=Decimal("2.00"), model="claude-haiku-4-5-20251001"))
    ledger.record(_entry(cost=Decimal("0.25"), model="claude-opus-4-7"))
    rollup = ledger.by_model()
    assert rollup == {
        "claude-opus-4-7": Decimal("1.25"),
        "claude-haiku-4-5-20251001": Decimal("2.00"),
    }


def test_cost_ledger_by_provider_rollup() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("3.00"), provider="anthropic"))
    ledger.record(_entry(cost=Decimal("0.10"), provider="anthropic"))
    rollup = ledger.by_provider()
    assert rollup == {"anthropic": Decimal("3.10")}


def test_cost_ledger_per_attempt_entries_for_retry_succeed() -> None:
    """F4: a logical call succeeding on retry 2 produces 3 entries.

    Two ``FAILED`` attempts + one ``SUCCESS`` attempt; all three are
    billed by the vendor and recorded as distinct entries. ``total_usd``
    must include every attempt — that is the point of the per-attempt
    discipline. A future "deduplicate to one entry per logical call"
    refactor must fail this test loudly.
    """
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("0.10"), outcome=CallOutcome.FAILED, purpose="attempt 1"))
    ledger.record(_entry(cost=Decimal("0.10"), outcome=CallOutcome.FAILED, purpose="attempt 2"))
    ledger.record(_entry(cost=Decimal("0.10"), outcome=CallOutcome.SUCCESS, purpose="attempt 3"))
    assert len(ledger.entries) == 3
    outcomes = [e.outcome for e in ledger.entries]
    assert outcomes.count(CallOutcome.FAILED) == 2
    assert outcomes.count(CallOutcome.SUCCESS) == 1
    assert ledger.total_usd == Decimal("0.30")


def test_cost_ledger_remaining_under_cap_none_when_no_cap() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("5.00")))
    assert ledger.remaining_under_cap() is None


def test_cost_ledger_remaining_under_cap_positive_when_under() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=Decimal("10.00"))
    ledger.record(_entry(cost=Decimal("3.00")))
    assert ledger.remaining_under_cap() == Decimal("7.00")


def test_cost_ledger_remaining_under_cap_negative_after_overrun() -> None:
    """F1: overrun returns a negative Decimal — no exception is raised.

    Budget-overrun handling belongs to the framework, not the provider
    layer (provider-interface.md §5.3). The brief's mention of a
    ``BudgetExceeded`` exception conflicts with that ownership rule, so
    this module deliberately exposes ``remaining_under_cap()`` only.
    """
    ledger = CostLedger(run_id="r1", cap_usd=Decimal("10.00"))
    ledger.record(_entry(cost=Decimal("12.00")))
    ledger.record(_entry(cost=Decimal("3.00")))
    remaining = ledger.remaining_under_cap()
    assert remaining == Decimal("-5.00")


def test_cost_ledger_zero_cost_call_recorded() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("0")))
    ledger.record(_entry(cost=Decimal("1.00")))
    assert len(ledger.entries) == 2
    assert ledger.total_usd == Decimal("1.00")


def test_cost_ledger_entries_returns_copy() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=None)
    ledger.record(_entry(cost=Decimal("1.00")))
    snapshot = ledger.entries
    snapshot.clear()
    assert len(ledger.entries) == 1


def test_cost_ledger_to_report_block_shape() -> None:
    ledger = CostLedger(run_id="r1", cap_usd=Decimal("100"))
    ledger.record(
        _entry(
            cost=Decimal("1.00"),
            agent=AgentLabel.PLANNER,
            provider="anthropic",
            model="claude-opus-4-7",
        )
    )
    ledger.record(
        _entry(
            cost=Decimal("2.00"),
            agent=AgentLabel.CRITIC,
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
        )
    )
    block = ledger.to_report_block()
    assert isinstance(block, CostReportBlock)
    assert block.total_usd == Decimal("3.00")
    assert block.by_agent == {
        AgentLabel.PLANNER: Decimal("1.00"),
        AgentLabel.CRITIC: Decimal("2.00"),
    }
    assert block.by_model == {
        "claude-opus-4-7": Decimal("1.00"),
        "claude-haiku-4-5-20251001": Decimal("2.00"),
    }
    assert block.by_provider == {"anthropic": Decimal("3.00")}
    assert len(block.entries) == 2
