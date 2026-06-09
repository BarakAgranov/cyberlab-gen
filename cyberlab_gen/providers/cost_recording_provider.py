"""A ``Provider`` wrapper that records each call's cost and enforces the ceiling.

Architectural source: ``provider-interface.md §5`` (cost tracking; the framework —
not the provider — owns budget-overrun decisions, §5.3), ADR 0030 (real per-call
recording), ADR 0033 (billed-on-raise), ADR 0038 (per-call cost visibility + the
mid-run catastrophe ceiling). Lives in the package (not ``eval/``) so both the eval
harness and the ``extract`` CLI feed the same ledger.

Two jobs, both at the single point that sees every billed call:

1. **Cost visibility (the primary requirement).** After each billed call — success
   *and* billed-but-raised failure — it records a per-attempt :class:`CostLedgerEntry`
   and logs one line: model, input/output/cache tokens, the cost of *that* call, and
   the running cumulative total and call count. After one or two runs the user can
   answer "where does the money go?" from the run log alone.
2. **The catastrophe ceiling.** After recording *any* billed call — success **and**
   billed-but-raised failure — if cumulative spend has crossed ``ledger.cap_usd`` (the
   high backstop, default ``DEFAULT_CATASTROPHE_CEILING_USD``), it raises
   :class:`BudgetExceeded` to abort the run immediately. The ledger never raises
   (``§5.3``); this framework-side wrapper makes the decision. Enforcing on the failure
   path too is essential, and corrects ADR 0038's original premise (amended by ADR
   0047): a billed *failure* does **not** reliably halt the run. A ``MalformedOutput``
   is caught and retried by the structural-retry / refinement machinery (``call_surface``
   §, ``architecture.md §1.7``), so its ``ProviderError`` is absorbed while each retry
   bills again — spend accumulates unbounded if the ceiling is checked only on success.
   ``BudgetExceeded`` is a ``HardFailure`` (not a ``MalformedOutput``), so it escapes
   that machinery and halts; the original error is preserved as its ``cause``, never
   masked. Below the ceiling the failure path re-raises the original error unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.errors import BudgetExceeded, ProviderError
from cyberlab_gen.providers.base import Provider
from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedgerEntry

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from pydantic import BaseModel

    from cyberlab_gen.providers.base import (
        AgentLabel,
        CapabilityHint,
        Message,
        ProviderResponse,
        TokenUsage,
        ToolDefinition,
        ToolExecutor,
    )
    from cyberlab_gen.providers.cost_ledger import CostLedger

logger = logging.getLogger(__name__)


class CostRecordingProvider(Provider):
    """Wrap a ``Provider`` so every billed call is recorded, logged, and capped.

    Delegates the full call surface to ``inner``. Attribution (``agent_label``) is the
    call surface's job, which this wrapper is part of.
    """

    def __init__(
        self,
        inner: Provider,
        ledger: CostLedger,
        *,
        purpose: str = "eval",
        on_call: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._purpose = purpose
        # Optional live echo of each per-call cost line (ADR 0038 visibility). The run
        # log always gets the full INFO line; ``on_call`` lets a caller ALSO surface a
        # concise line live (e.g. the CLI's ``--show-cost`` → stderr), since the console
        # is WARNING-only by default (ADR 0037). ``None`` ⇒ log only (unchanged).
        self._on_call = on_call

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        model: str,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        try:
            response = await self._inner.complete(
                messages,
                output_schema=output_schema,
                capability=capability,
                model=model,
                agent_label=agent_label,
                max_tokens=max_tokens,
            )
        except ProviderError as exc:
            self._record_billed_failure(exc, agent_label=agent_label, capability=capability)
            raise
        self._record(response, agent_label=agent_label, capability=capability)
        return response

    async def complete_with_tools[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        model: str,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        agent_label: AgentLabel,
        max_iterations: int,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        try:
            response = await self._inner.complete_with_tools(
                messages,
                output_schema=output_schema,
                capability=capability,
                model=model,
                tools=tools,
                tool_executor=tool_executor,
                agent_label=agent_label,
                max_iterations=max_iterations,
                max_tokens=max_tokens,
            )
        except ProviderError as exc:
            self._record_billed_failure(exc, agent_label=agent_label, capability=capability)
            raise
        self._record(response, agent_label=agent_label, capability=capability)
        return response

    def _record[T_Output: BaseModel](
        self,
        response: ProviderResponse[T_Output],
        *,
        agent_label: AgentLabel,
        capability: CapabilityHint,
    ) -> None:
        self._ledger.record(
            CostLedgerEntry(
                timestamp=datetime.now(UTC),
                agent_label=agent_label,
                provider=response.provider,
                model=response.model,
                capability=capability,
                usage=response.usage,
                outcome=CallOutcome.SUCCESS,
                purpose=self._purpose,
            )
        )
        self._log_call(agent_label, response.model, response.usage, CallOutcome.SUCCESS)
        self._enforce_ceiling(agent_label, response.usage, response.model)

    def _record_billed_failure(
        self,
        exc: ProviderError,
        *,
        agent_label: AgentLabel,
        capability: CapabilityHint,
    ) -> None:
        """Record the vendor-billed usage of a call that raised (ADR 0033).

        A call with no attached usage/model (it failed before any vendor call, or the
        layer below did not attach) records nothing. Recorded as ``FAILED`` so the
        ledger distinguishes wasted spend from healthy spend while ``total_usd`` still
        counts it.

        The catastrophe ceiling is enforced here too (ADR 0047, amending ADR 0038): a
        run dominated by billed failures must be bounded the same as a succeeding one,
        because a failed call's ``ProviderError`` is **not** guaranteed to halt the run
        — a ``MalformedOutput`` is retried by layers above this wrapper while each
        attempt bills again. When cumulative spend crosses the ceiling this raises
        :class:`BudgetExceeded` with ``exc`` threaded through as the ``cause`` so the
        original failure is preserved, not masked. Below the ceiling nothing is raised
        here and the caller re-raises ``exc`` unchanged.
        """
        if exc.usage is None or exc.model is None:
            return
        self._ledger.record(
            CostLedgerEntry(
                timestamp=datetime.now(UTC),
                agent_label=agent_label,
                provider=self._inner.name,
                model=exc.model,
                capability=capability,
                usage=exc.usage,
                outcome=CallOutcome.FAILED,
                purpose=self._purpose,
            )
        )
        self._log_call(agent_label, exc.model, exc.usage, CallOutcome.FAILED)
        self._enforce_ceiling(agent_label, exc.usage, exc.model, cause=exc)

    def _log_call(
        self, agent_label: AgentLabel, model: str, usage: TokenUsage, outcome: CallOutcome
    ) -> None:
        n = len(self._ledger.entries)
        logger.info(
            "LLM call #%d [%s]: agent=%s model=%s in=%d out=%d cache_r=%d cache_w=%d "
            "cost=$%s cumulative=$%s",
            n,
            outcome.value,
            agent_label.value,
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            usage.cost_usd,
            self._ledger.total_usd,
        )
        if self._on_call is not None:
            self._on_call(
                f"  llm call #{n} [{outcome.value}]: {model} "
                f"cost=${usage.cost_usd} (cumulative ${self._ledger.total_usd})"
            )

    def _enforce_ceiling(
        self,
        agent_label: AgentLabel,
        usage: TokenUsage,
        model: str,
        *,
        cause: ProviderError | None = None,
    ) -> None:
        """Abort the run if cumulative spend crossed the catastrophe ceiling (ADR 0038).

        Called after every billed call — success (``_record``) and billed failure
        (``_record_billed_failure``) alike, ADR 0047 — so the ceiling bounds a
        failure-dominated run identically to a successful one. On the failure path the
        originating provider error is passed as ``cause`` so escalating to
        :class:`BudgetExceeded` chains it rather than masking it; on the success path
        ``cause`` is ``None``.
        """
        remaining = self._ledger.remaining_under_cap()
        if remaining is None or remaining > 0:
            return
        spent: Decimal = self._ledger.total_usd
        ceiling = self._ledger.cap_usd
        logger.error(
            "catastrophe cost ceiling hit: cumulative $%s reached the $%s ceiling after %d call(s)",
            spent,
            ceiling,
            len(self._ledger.entries),
        )
        budget_exceeded = BudgetExceeded(
            f"cumulative LLM spend ${spent} reached the ${ceiling} catastrophe ceiling after "
            f"{len(self._ledger.entries)} billed call(s); aborting to stop a runaway. This is a "
            f"HIGH backstop, not an everyday cap — now that per-call costs are visible in the run "
            f"log, set an informed limit with --max-llm-cost.",
            spent_usd=spent,
            ceiling_usd=ceiling,
            usage=usage,
            model=model,
            cause=cause,
        )
        if cause is None:
            raise budget_exceeded
        raise budget_exceeded from cause


__all__ = ["CostRecordingProvider"]
