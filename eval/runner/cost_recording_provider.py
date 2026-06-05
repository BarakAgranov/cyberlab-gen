"""A ``Provider`` wrapper that records each call's real cost into a ``CostLedger``.

Architectural source: ``provider-interface.md §5`` (cost tracking; the call
surface owns ledger attribution, not the provider), ADR 0030.

Why this exists: in the eval the per-run :class:`~cyberlab_gen.providers.cost_ledger.CostLedger`
was never fed — the pipeline ``del``s the ledger it is handed (``cli/extract.py``)
and the Anthropic adapter sums usage into a private accumulator, so
``ledger.total_usd`` stayed ``0`` and any cost cap built on it was hollow. This
thin wrapper closes that gap honestly: it delegates every call to the real
provider and records the returned :class:`~cyberlab_gen.providers.base.ProviderResponse`'s
costed ``usage`` into the ledger, so the eval's cumulative cost — and therefore
the cost cap — reflects real spend. Full ledger→pipeline wiring (per-attempt
rows threaded through the orchestrator) remains the deferred broader task; this
captures the per-call totals the cap needs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.errors import ProviderError
from cyberlab_gen.providers.base import Provider
from cyberlab_gen.providers.cost_ledger import CallOutcome, CostLedgerEntry

if TYPE_CHECKING:
    from pydantic import BaseModel

    from cyberlab_gen.providers.base import (
        AgentLabel,
        CapabilityHint,
        Message,
        ProviderResponse,
        ToolDefinition,
        ToolExecutor,
    )
    from cyberlab_gen.providers.cost_ledger import CostLedger


class CostRecordingProvider(Provider):
    """Wrap a ``Provider`` so every completed call records its cost into ``ledger``.

    Delegates the full call surface to ``inner`` and, after each successful call,
    appends a :class:`CostLedgerEntry` carrying the response's costed ``usage`` so
    ``ledger.total_usd`` tracks real spend (ADR 0030). Attribution (``agent_label``)
    is the call surface's job, which this wrapper is.

    A call that ultimately *raises* a :class:`ProviderError` (a truncated/malformed
    emit that exhausted its retries, a tool loop that never converged) was still
    billed by the vendor for every attempt. The provider attaches that billed usage
    to the raised error (ADR 0033); this wrapper records it as a ``FAILED`` entry
    before re-raising, so the ledger — and the cost cap built on it — reflect real
    spend even when no response comes back. Without this, billed-but-raised calls
    were invisible and the reported cost under-counted the true spend.
    """

    def __init__(self, inner: Provider, ledger: CostLedger, *, purpose: str = "eval") -> None:
        self._inner = inner
        self._ledger = ledger
        self._purpose = purpose

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        try:
            response = await self._inner.complete(
                messages,
                output_schema=output_schema,
                capability=capability,
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

    def _record_billed_failure(
        self,
        exc: ProviderError,
        *,
        agent_label: AgentLabel,
        capability: CapabilityHint,
    ) -> None:
        """Record the vendor-billed usage of a call that raised (ADR 0033).

        The provider attaches accumulated billed ``usage`` + the resolved ``model``
        to a raised :class:`ProviderError`; a call with neither (it failed before any
        vendor call, or the layer below did not attach) records nothing. Recorded as
        ``FAILED`` so the ledger distinguishes wasted spend from healthy spend, while
        ``total_usd`` (and therefore the cost cap) still counts it.
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


__all__ = ["CostRecordingProvider"]
