"""Cost ledger and pricing-table support.

Architectural source: ``provider-interface.md`` §5 (cost tracking),
``pipeline.md`` §3.5 (per-model cost rationale), §3.6.3 (run-report cost
block). Phase 0 Task 5b.

Budget-overrun ownership: per ``provider-interface.md`` §5.3 the
**framework — not the provider — owns budget-overrun decisions**. This
module records usage, reports totals, and exposes ``remaining_under_cap()``
as an accessor. It deliberately does NOT define a ``BudgetExceeded``
exception or raise on overrun; the framework reads the accessor before
invoking the next stage and decides whether to interrupt.

Phase-0 scope:

- ``PricingTable``, ``ModelPricing`` deserialize ``pricing.yaml`` and
  back the per-model cost computation. ``ArtifactModel`` per ADR 0004
  with ``frozen=True`` layered on top — these are construct-once
  read-many objects, and ``extra="forbid"`` (inherited from
  ``ArtifactModel``) catches field typos at load time.
- ``CostLedgerEntry`` and ``CostReportBlock`` are run-report
  artifacts; ``ArtifactModel`` again per ADR 0004.
- ``CostLedger`` is a plain class because it accumulates mutable state
  (the entries list); it is not an artifact.
- ``compute_cost`` bills ``cache_write_tokens`` at the **5-minute**
  rate. ``TokenUsage`` currently has one ``cache_write_tokens`` field,
  not split 5min/1h. Recorded in the Phase-0 execution log; Phase 1's
  Anthropic adapter task revisits the split when the SDK reveals what
  cache-write info we get back.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import ConfigDict
from ruamel.yaml import YAML

from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    TokenUsage,
)
from cyberlab_gen.schemas.base import ArtifactModel

_PER_MILLION = Decimal(1_000_000)

#: The default **catastrophe ceiling** — a high, configurable backstop whose only
#: job is to stop a pathological runaway, NOT an everyday brake (ADR 0038). It is
#: deliberately high: a normal-but-failing run must not trip it. Once a user has
#: observed real per-run costs (now visible per-call), they should replace this with
#: an informed value via ``--max-llm-cost``. Enforced mid-run by the framework-side
#: ``CostRecordingProvider`` (the ledger itself never raises, ``§5.3``).
DEFAULT_CATASTROPHE_CEILING_USD = Decimal("25")


class ModelPricing(ArtifactModel):
    """Per-million-token rates for one (provider, model) pair.

    ``provider-interface.md`` §5.2. Five Decimal rates: standard input
    and output, plus three cache variants (read, 5-minute write, 1-hour
    write). ``frozen=True`` because the loaded table is read-many.
    """

    model_config = ConfigDict(frozen=True)

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5min: Decimal
    cache_write_1h: Decimal


class PricingTable(ArtifactModel):
    """Loaded ``pricing.yaml`` indexed by ``(provider, model)``.

    The on-disk YAML is a two-level mapping (``provider -> model -> rates``);
    in memory the same shape lives under the single ``rows`` field so the
    model can be validated by ``ArtifactModel.model_validate``. The loader
    in :func:`load_pricing_table` wraps the raw YAML into ``{"rows": ...}``.
    """

    model_config = ConfigDict(frozen=True)

    rows: dict[str, dict[str, ModelPricing]]

    def lookup(self, provider: str, model: str) -> ModelPricing:
        """Return the pricing row for ``(provider, model)``.

        Raises ``KeyError`` with a clear message naming both keys when
        either is missing.
        """
        try:
            return self.rows[provider][model]
        except KeyError as exc:
            raise KeyError(f"No pricing entry for provider={provider!r}, model={model!r}") from exc


def load_pricing_table() -> PricingTable:
    """Load the bundled ``cyberlab_gen/providers/pricing.yaml`` file.

    Phase 0: bundled-only. User overlay merge (``~/.cyberlab-gen/pricing.yaml``)
    is deferred to a later task; loaders are structured so an overlay layer
    can drop in without changing call sites.
    """
    path = Path(__file__).resolve().parent / "pricing.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))
    return PricingTable.model_validate({"rows": data or {}})


def compute_cost(
    table: PricingTable,
    *,
    provider: str,
    model: str,
    usage: TokenUsage,
) -> Decimal:
    """Compute USD cost for a single call from token counts and pricing.

    Per-million-token math: each token count is multiplied by its rate
    and divided by 1,000,000. ``cache_write_tokens`` is billed at the
    5-minute rate (Phase-0 decision; see module docstring).

    Raises ``KeyError`` (from :meth:`PricingTable.lookup`) when the
    ``(provider, model)`` pair is not in the table.
    """
    pricing = table.lookup(provider, model)
    total = (
        Decimal(usage.input_tokens) * pricing.input
        + Decimal(usage.output_tokens) * pricing.output
        + Decimal(usage.cache_read_tokens) * pricing.cache_read
        + Decimal(usage.cache_write_tokens) * pricing.cache_write_5min
    ) / _PER_MILLION
    return total


class CallOutcome(StrEnum):
    """How a single provider-call attempt ended.

    Per-attempt outcomes let the eval harness compute retry rates per
    agent and distinguish wasted spend (failed-after-retries) from
    healthy spend (success-first-try or retry-succeeded). Every billed
    attempt produces one :class:`CostLedgerEntry`; a logical call that
    succeeds on retry 2 produces three entries (two ``FAILED``, one
    ``SUCCESS``) summing to the full vendor-billed cost.
    """

    SUCCESS = "success"
    FAILED = "failed"


class CostLedgerEntry(ArtifactModel):
    """One billed provider-call attempt.

    ``provider-interface.md`` §5.2. Entries are per-attempt, not
    per-logical-call: a retry-2-success call produces three entries.
    This makes retry rates per agent computable from the ledger alone.
    """

    timestamp: datetime
    agent_label: AgentLabel
    provider: str
    model: str
    capability: CapabilityHint
    usage: TokenUsage
    outcome: CallOutcome
    purpose: str


class CostReportBlock(ArtifactModel):
    """Serialized cost section of the run report (``pipeline.md`` §3.6.3)."""

    total_usd: Decimal
    by_agent: dict[AgentLabel, Decimal]
    by_model: dict[str, Decimal]
    by_provider: dict[str, Decimal]
    entries: list[CostLedgerEntry]


class CostLedger:
    """Accumulates token usage and cost across a single run.

    Created at run start, attached to the run context, read by the
    framework when deciding whether to emit a budget-overrun interrupt
    (``pipeline.md`` §3.1.1). The provider layer records usage; the
    framework owns the cap decision (``provider-interface.md`` §5.3).

    Plain class, not a Pydantic model — the entries list is mutable
    state appended over the run's lifetime. Serialization happens via
    :meth:`to_report_block`, which produces an artifact-bound
    :class:`CostReportBlock`.
    """

    def __init__(self, run_id: str, cap_usd: Decimal | None) -> None:
        self.run_id = run_id
        self.cap_usd = cap_usd
        self._entries: list[CostLedgerEntry] = []

    def record(self, entry: CostLedgerEntry) -> None:
        """Append a per-attempt entry to the ledger."""
        self._entries.append(entry)

    @property
    def entries(self) -> list[CostLedgerEntry]:
        """All recorded entries, in record order. Returned list is a copy."""
        return list(self._entries)

    @property
    def total_usd(self) -> Decimal:
        """Sum of ``usage.cost_usd`` over every recorded entry."""
        return sum((e.usage.cost_usd for e in self._entries), start=Decimal("0"))

    def remaining_under_cap(self) -> Decimal | None:
        """``None`` if no cap; else ``cap_usd - total_usd``.

        May be negative once the cap is exceeded. **Does not raise** —
        budget-overrun decisions belong to the framework, not the
        provider layer (``provider-interface.md`` §5.3).
        """
        if self.cap_usd is None:
            return None
        return self.cap_usd - self.total_usd

    def by_agent(self) -> dict[AgentLabel, Decimal]:
        """Per-agent cost rollup."""
        return self._rollup(lambda e: e.agent_label)

    def by_model(self) -> dict[str, Decimal]:
        """Per-model cost rollup."""
        return self._rollup(lambda e: e.model)

    def by_provider(self) -> dict[str, Decimal]:
        """Per-provider cost rollup."""
        return self._rollup(lambda e: e.provider)

    def to_report_block(self) -> CostReportBlock:
        """Serialize the ledger as a :class:`CostReportBlock`."""
        return CostReportBlock(
            total_usd=self.total_usd,
            by_agent=self.by_agent(),
            by_model=self.by_model(),
            by_provider=self.by_provider(),
            entries=list(self._entries),
        )

    def _rollup[K](self, key: Callable[[CostLedgerEntry], K]) -> dict[K, Decimal]:
        out: dict[K, Decimal] = {}
        for entry in self._entries:
            k = key(entry)
            out[k] = out.get(k, Decimal("0")) + entry.usage.cost_usd
        return out


__all__ = [
    "DEFAULT_CATASTROPHE_CEILING_USD",
    "CallOutcome",
    "CostLedger",
    "CostLedgerEntry",
    "CostReportBlock",
    "ModelPricing",
    "PricingTable",
    "compute_cost",
    "load_pricing_table",
]
