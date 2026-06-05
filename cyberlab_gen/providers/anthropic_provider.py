"""Anthropic provider — the real call bodies (Phase 1).

Architectural source: ``provider-interface.md`` §4 (call surface), §6 (error
semantics), §6.2 (malformed-output retry — 2 attempts per ADR 0018), §3.4
(capability resolution); ``pipeline.md`` §3.7 (transient retry). ADR 0018
(two-layer structural-retry budget), ADR 0027 (this adapter's design decisions).

What this module does (and what it deliberately does not):

- **Structured output** is obtained by *forced tool use*: the declared
  ``output_schema`` is exposed to the model as a single "emit" tool and
  ``tool_choice`` forces a call to it. The tool's ``input`` is parsed with
  ``output_schema.model_validate``. This is the robust, documented way to get
  schema-bound output from Claude; we do not parse free text / fenced JSON.
  (ADR 0027.)
- **Model resolution.** The ``Provider`` ABC hands the adapter a
  ``CapabilityHint``, not a model id. The adapter resolves it the same way
  ``ProviderRegistry`` does: the first ``anthropic`` entry for that capability in
  the bundled ``model_rankings.yaml``. The adapter is only ever invoked when the
  registry already resolved the capability to an ``anthropic`` model, so the two
  agree by construction.
- **Cost.** ``ProviderResponse.usage`` carries the **accumulated** token counts
  across every billed vendor call in the invocation (malformed retries; every
  tool-loop iteration), with ``cost_usd`` computed via
  ``cost_ledger.compute_cost``. The locked ``ProviderResponse`` exposes a single
  ``TokenUsage``; summing is the only honest single figure when a tool loop makes
  many calls. Per-attempt ``CostLedgerEntry`` rows (``cost_ledger`` §) have **no
  integration point yet** — ``AgentRunner`` records no ledger entries — so the
  adapter does not emit them; flagged for the orchestrator/ledger-wiring task.
- **No sampling params** (temperature/top_p/top_k) are sent — deliberate, per the
  ``Provider`` ABC docstring and ``provider-interface.md`` §4.1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import anthropic
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.errors import (
    EmitTruncated,
    HardFailure,
    MalformedOutput,
    ProviderError,
    ToolLoopError,
    TransientFailure,
)
from cyberlab_gen.providers.base import (
    AgentLabel,
    CapabilityHint,
    Message,
    MessageRole,
    Provider,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolExecutor,
)
from cyberlab_gen.providers.cost_ledger import PricingTable, compute_cost, load_pricing_table
from cyberlab_gen.providers.ranking import load_model_rankings
from cyberlab_gen.providers.retries import (
    MALFORMED_OUTPUT_RETRIES,
    TRANSIENT_RETRIES,
    RetryStrategy,
)

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "anthropic"

#: Default output cap when a caller passes ``max_tokens=None``. The Anthropic API
#: requires ``max_tokens``; agents that need more (the Extractor's AttackSpec)
#: pass an explicit value.
DEFAULT_MAX_TOKENS = 4096

#: Anthropic tool names must match ``^[a-zA-Z0-9_-]{1,64}$``.
_EMIT_TOOL_PREFIX = "emit_"

#: Set this env var (to any non-empty value) to dump the message array to stderr
#: on a non-retryable 4xx — the tool-loop-400 diagnostic. Off by default so normal
#: runs are quiet (ADR 0031); the structural fix means it should no longer fire.
_DEBUG_TOOL_LOOP_ENV = "CYBERLAB_GEN_DEBUG_TOOL_LOOP"

#: Set this env var (to any non-empty value) to dump the model's actual emitted
#: tool arguments to stderr whenever an emit fails schema validation — the
#: content-failure diagnostic (ADR 0032). Off by default. Unlike the tool-loop
#: dump (ids only), this prints the raw emitted *content* (e.g. an in-scope
#: AttackSpec that omitted ``chain``) so a repeating validation failure can be
#: diagnosed from real data without re-running at cost.
_DEBUG_EMIT_ENV = "CYBERLAB_GEN_DEBUG_EMIT"

#: Set this env var to a **directory** to capture the RAW partial emit whenever an
#: emit is truncated at ``max_tokens`` (ADR 0035). A truncated emit never validates,
#: so its content is normally discarded — a maintainer can never see *what* the model
#: produced (tight-but-large, or bloated?). With this set, the raw partial arguments
#: are written to ``<dir>/<schema>-truncated.json`` *before* ``EmitTruncated`` is
#: raised. This is **additive** — it does not change the ADR-0033 halt (the run still
#: fails fast, ships nothing); it only persists the content for reading. Unset (the
#: default) ⇒ nothing is written. To capture one Extractor run, point it at the eval
#: specs dir: ``CYBERLAB_GEN_EMIT_DUMP_DIR=eval/reports/specs``.
_EMIT_DUMP_DIR_ENV = "CYBERLAB_GEN_EMIT_DUMP_DIR"


@dataclass
class _UsageAccumulator:
    """Sums token counts across every billed vendor call in one invocation.

    The locked ``ProviderResponse`` carries a single ``TokenUsage``; a
    ``complete_with_tools`` loop makes one billed call per iteration plus any
    malformed retries, so the honest cost is the sum. Cost is computed once at
    :meth:`finalize` from the accumulated counts.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add(self, usage: object) -> None:
        """Accumulate one vendor response's ``usage`` block."""
        self.input_tokens += _int_attr(usage, "input_tokens")
        self.output_tokens += _int_attr(usage, "output_tokens")
        self.cache_read_tokens += _int_attr(usage, "cache_read_input_tokens")
        self.cache_write_tokens += _cache_write_tokens(usage)
        self.calls += 1

    def finalize(self, *, model: str, pricing: PricingTable) -> TokenUsage:
        """Build the ``TokenUsage`` with cost computed from the pricing table.

        Raises ``HardFailure`` when ``model`` is absent from the pricing table:
        a billed call whose cost we cannot compute would otherwise silently
        report zero and corrupt budget tracking (``provider-interface.md`` §5).
        """
        usage = TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cost_usd=Decimal("0"),
        )
        try:
            cost = compute_cost(pricing, provider=_PROVIDER_NAME, model=model, usage=usage)
        except KeyError as exc:
            raise HardFailure(
                f"No pricing entry for the resolved model {model!r}; refusing to report "
                f"zero cost for a billed call. Add it to cyberlab_gen/providers/pricing.yaml "
                f"or fix the model_rankings.yaml entry.",
                cause=exc,
            ) from exc
        return usage.model_copy(update={"cost_usd": cost})


def _int_attr(obj: object, name: str) -> int:
    value = getattr(obj, name, 0)
    return int(value) if isinstance(value, int) else 0


def _cache_write_tokens(usage: object) -> int:
    """Cache-*write* tokens from a vendor usage block.

    Cache-write-split finding (work item 5, ADR 0027): the Anthropic SDK exposes
    a 5-minute/1-hour split via ``usage.cache_creation.ephemeral_5m_input_tokens``
    / ``.ephemeral_1h_input_tokens`` (the 1-hour-cache beta), with the flat
    ``usage.cache_creation_input_tokens`` as the total. This adapter never sets
    ``cache_control`` (no prompt caching in Phase 1), so cache-write is normally
    0; when prompt caching is added it defaults to the 5-minute TTL, matching the
    single ``TokenUsage.cache_write_tokens`` field that ``compute_cost`` bills at
    the 5-minute rate. We therefore keep the single field and use the flat total.
    If a 1-hour-cache write is ever observed we log it so the deferred split is
    revisited rather than mis-billed.
    """
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is not None:
        one_hour = _int_attr(cache_creation, "ephemeral_1h_input_tokens")
        if one_hour:
            logger.warning(
                "anthropic returned %d one-hour cache-write tokens; these are billed at the "
                "5-minute rate (single TokenUsage.cache_write_tokens field) — revisit the "
                "5min/1h split (ADR 0027) before relying on 1-hour caching",
                one_hour,
            )
    return _int_attr(usage, "cache_creation_input_tokens")


class AnthropicProvider(Provider):
    """Anthropic adapter implementing the ``Provider`` call surface.

    The vendor client is injectable (``client`` constructor arg) so the tool
    loop, retry, and cost logic are unit-testable offline against a fake; the
    live path constructs a real ``anthropic.AsyncAnthropic`` lazily (it reads
    ``ANTHROPIC_API_KEY`` from the environment). Retry strategies are injectable
    too so tests run with zero backoff delay.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,  # noqa: ANN401 -- anthropic.AsyncAnthropic | test fake; SDK is untyped enough that a precise type buys nothing
        transient_retries: RetryStrategy = TRANSIENT_RETRIES,
        malformed_retries: RetryStrategy = MALFORMED_OUTPUT_RETRIES,
    ) -> None:
        self._client = client
        self._transient = transient_retries
        self._malformed = malformed_retries
        self._rankings = load_model_rankings()
        self._pricing: PricingTable | None = None

    @property
    def name(self) -> str:
        return _PROVIDER_NAME

    # --- public call surface ----------------------------------------------

    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        del agent_label  # used only for ledger attribution, which the call surface owns
        model = self._resolve_model(capability)
        system, base = _translate_messages(messages)
        emit_tool = _emit_tool(output_schema)
        acc = _UsageAccumulator()

        output = await self._extract_structured(
            base_messages=base,
            system=system,
            model=model,
            output_schema=output_schema,
            emit_tool=emit_tool,
            acc=acc,
            max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
        )
        raw_text = output.model_dump_json()
        usage = acc.finalize(model=model, pricing=self._pricing_table())
        final = Message(role=MessageRole.ASSISTANT, content=raw_text)
        return ProviderResponse[T_Output](
            output=output,
            raw_text=raw_text,
            usage=usage,
            model=model,
            provider=_PROVIDER_NAME,
            conversation=[*messages, final],
            tool_calls=[],
        )

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
        del agent_label
        model = self._resolve_model(capability)
        system, convo = _translate_messages(messages)
        emit_tool = _emit_tool(output_schema)
        emit_name = emit_tool["name"]
        loop_tools = [_translate_tool(t) for t in tools] + [emit_tool]
        tokens = max_tokens or DEFAULT_MAX_TOKENS

        acc = _UsageAccumulator()
        trace: list[Message] = list(messages)
        calls_made: list[ToolCall] = []

        for _iteration in range(max_iterations):
            response = await self._create(
                model=model,
                system=system,
                messages=convo,
                tools=loop_tools,
                tool_choice={"type": "auto"},
                max_tokens=tokens,
                acc=acc,
            )
            content = list(getattr(response, "content", []))
            real_uses = [b for b in content if _is_tool_use(b) and _block_name(b) != emit_name]
            emit_use = next(
                (b for b in content if _is_tool_use(b) and _block_name(b) == emit_name), None
            )

            if emit_use is not None and not real_uses:
                # Model chose to finish. Parse its emit call; on invalid arguments,
                # fall back to a forced extract (with malformed-retry). CRITICAL
                # (ADR 0031): the emit block is a ``tool_use`` too — we must ANSWER it
                # with a ``tool_result`` before the forced extract's next API call.
                # The old code appended the emit turn and handed it to
                # ``_extract_structured`` *unanswered*, so its first request carried a
                # dangling trailing ``tool_use`` → the 400 that killed every run.
                try:
                    output = output_schema.model_validate(_block_input(emit_use))
                except PydanticValidationError as exc:
                    triggering_error = _short_error(exc)
                    out_tokens = _int_attr(getattr(response, "usage", None), "output_tokens")
                    _dump_emit_on_validation_error(
                        _block_input(emit_use),
                        schema_name=output_schema.__name__,
                        exc=exc,
                        stop_reason=_stop_reason(response),
                        output_tokens=out_tokens,
                        max_tokens=tokens,
                    )
                    # Truncation halt (ADR 0033): the emit failed validation because it
                    # stopped at max_tokens, not because the model chose to malform it.
                    # The forced-extract fallback below would just regenerate the same
                    # ~max_tokens-token output and truncate again — so halt immediately
                    # with the billed usage attached, rather than burn the retry budget.
                    if _is_truncated(response):
                        raise self._with_usage(
                            _emit_truncated(
                                schema_name=output_schema.__name__,
                                max_tokens=tokens,
                                output_tokens=out_tokens,
                            ),
                            model=model,
                            acc=acc,
                        ) from exc
                    convo.append({"role": "assistant", "content": content})
                    convo.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": _block_id(emit_use),
                                    "content": (
                                        f"Schema validation failed: {triggering_error}. "
                                        f"Call {emit_name} again with corrected arguments."
                                    ),
                                    "is_error": True,
                                }
                            ],
                        }
                    )
                    output = await self._extract_structured(
                        base_messages=convo,
                        system=system,
                        model=model,
                        output_schema=output_schema,
                        emit_tool=emit_tool,
                        acc=acc,
                        max_tokens=tokens,
                        prior_parse_error=triggering_error,
                    )
                return self._finish(
                    output=output, model=model, trace=trace, calls_made=calls_made, acc=acc
                )

            if not real_uses:
                # end_turn with text only (no tool calls): coerce a final structured
                # answer with a forced emit call (+ malformed retry).
                convo.append({"role": "assistant", "content": content or _text_of(response)})
                output = await self._extract_structured(
                    base_messages=convo,
                    system=system,
                    model=model,
                    output_schema=output_schema,
                    emit_tool=emit_tool,
                    acc=acc,
                    max_tokens=tokens,
                )
                return self._finish(
                    output=output, model=model, trace=trace, calls_made=calls_made, acc=acc
                )

            # The model invoked real tools. Anthropic's contract (ADR 0029): the
            # user message that follows an assistant turn with N ``tool_use`` blocks
            # must contain a ``tool_result`` for EVERY one of those ids, in order,
            # before any other content. So iterate *all* tool_use blocks in the
            # turn — not just the real ones — and answer each. A dropped or
            # mis-ordered block (the old loop orphaned a co-emitted emit call, and
            # aborted on an executor exception) is the 400 that killed every run.
            convo.append({"role": "assistant", "content": content})
            tool_use_blocks = [b for b in content if _is_tool_use(b)]

            result_blocks: list[dict[str, Any]] = []
            turn_calls: list[ToolCall] = []
            tool_trace: list[Message] = []
            for block in tool_use_blocks:
                call_id = _block_id(block)
                if _block_name(block) == emit_name:
                    # A premature emit alongside real tool calls: it cannot be
                    # executed (its arguments were formed before these results), but
                    # it still needs a tool_result so nothing dangles. Nudge the
                    # model to re-emit once it has seen the results, then loop.
                    result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": (
                                f"Review the tool results in this message, then call "
                                f"{emit_name} again with your final answer."
                            ),
                            "is_error": False,
                        }
                    )
                    continue
                call = ToolCall(
                    call_id=call_id,
                    tool_name=_block_name(block),
                    arguments=_block_input(block),
                )
                turn_calls.append(call)
                content_text, is_error = await _execute_tool(tool_executor, call)
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": content_text,
                        "is_error": is_error,
                    }
                )
                tool_trace.append(
                    Message(role=MessageRole.TOOL, content=content_text, tool_call_id=call_id)
                )

            trace.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=_text_of(response),
                    tool_calls=turn_calls,
                )
            )
            trace.extend(tool_trace)
            calls_made.extend(turn_calls)
            convo.append({"role": "user", "content": result_blocks})

        raise self._with_usage(
            ToolLoopError(
                f"model still requesting tools after max_iterations={max_iterations} "
                f"(capability={capability.value!r}, model={model!r})"
            ),
            model=model,
            acc=acc,
        )

    # --- structured-output extraction (forced tool use + malformed retry) --

    async def _extract_structured[T_Output: BaseModel](
        self,
        *,
        base_messages: list[dict[str, Any]],
        system: str,
        model: str,
        output_schema: type[T_Output],
        emit_tool: dict[str, Any],
        acc: _UsageAccumulator,
        max_tokens: int,
        prior_parse_error: str | None = None,
    ) -> T_Output:
        """Force the emit tool and parse its arguments, retrying on malformed output.

        Retries up to ``self._malformed.max_attempts`` (2 by default, per ADR
        0018), re-prompting with the parse error as a ``tool_result`` error block
        each time. On exhaustion raises ``MalformedOutput``
        (``provider-interface.md`` §6.2).

        ``prior_parse_error`` is the parse error (if any) that triggered this forced
        extract — e.g. the finish-turn emit that failed validation in
        ``complete_with_tools``. It seeds the no-progress check (ADR 0032): if the
        model reproduces the *identical* error, this bails immediately rather than
        spending the rest of the budget on a failure that is provably not
        converging. The raised ``MalformedOutput`` message is kept deterministic
        for a given error (no varying attempt count) so the call surface's own
        no-progress bail can recognise the same failure across stage attempts.
        """
        emit_name = emit_tool["name"]
        working = list(base_messages)
        parse_error = "unknown"
        prev_error = prior_parse_error
        for _attempt in range(self._malformed.max_attempts):
            response = await self._create(
                model=model,
                system=system,
                messages=working,
                tools=[emit_tool],
                tool_choice={"type": "tool", "name": emit_name},
                max_tokens=max_tokens,
                acc=acc,
            )
            content = list(getattr(response, "content", []))
            emit_use = next(
                (b for b in content if _is_tool_use(b) and _block_name(b) == emit_name), None
            )
            if emit_use is None:
                parse_error = "model did not return the forced emit tool call"
            else:
                try:
                    return output_schema.model_validate(_block_input(emit_use))
                except PydanticValidationError as exc:
                    parse_error = _short_error(exc)
                    out_tokens = _int_attr(getattr(response, "usage", None), "output_tokens")
                    _dump_emit_on_validation_error(
                        _block_input(emit_use),
                        schema_name=output_schema.__name__,
                        exc=exc,
                        stop_reason=_stop_reason(response),
                        output_tokens=out_tokens,
                        max_tokens=max_tokens,
                    )
                    # Truncation halt (ADR 0033): a forced-extract emit cut off at
                    # max_tokens will truncate again on the next attempt — bail out of
                    # the malformed-retry loop immediately instead of paying for a
                    # doomed regeneration. Raised past the no-progress bail below (which
                    # needs two attempts to see a repeat); truncation is known on the
                    # first. Billed usage attached for the cost ledger.
                    if _is_truncated(response):
                        raise self._with_usage(
                            _emit_truncated(
                                schema_name=output_schema.__name__,
                                max_tokens=max_tokens,
                                output_tokens=out_tokens,
                            ),
                            model=model,
                            acc=acc,
                        ) from exc
            # No-progress early-bail (ADR 0032): the model reproduced the identical
            # error (including the one that triggered this forced extract) — paying
            # for more retries cannot help. A *different* error may signal
            # convergence, so the budget is only abandoned on an exact repeat.
            if parse_error == prev_error:
                logger.warning(
                    "anthropic structured output reproduced the identical error; aborting "
                    "malformed-retry early (model=%s): %s",
                    model,
                    parse_error,
                )
                break
            prev_error = parse_error
            if emit_use is None:
                working = [
                    *working,
                    {"role": "assistant", "content": content or _text_of(response)},
                    {"role": "user", "content": f"You must call {emit_name}. {parse_error}"},
                ]
            else:
                working = [
                    *working,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": _block_id(emit_use),
                                "content": f"Schema validation failed: {parse_error}. "
                                f"Call {emit_name} again with corrected arguments.",
                                "is_error": True,
                            }
                        ],
                    },
                ]
        raise self._with_usage(
            MalformedOutput(
                f"structured output did not validate against {output_schema.__name__}; "
                f"last error: {parse_error}"
            ),
            model=model,
            acc=acc,
        )

    def _finish[T_Output: BaseModel](
        self,
        *,
        output: T_Output,
        model: str,
        trace: list[Message],
        calls_made: list[ToolCall],
        acc: _UsageAccumulator,
    ) -> ProviderResponse[T_Output]:
        """Assemble the final ``ProviderResponse`` with accumulated, costed usage.

        ``conversation`` is the full trace (input + every intermediate
        assistant tool-call turn and tool-result turn) plus the final assistant
        message carrying the structured output as text — the trace
        search-before-claim and the Jury's provenance re-verification inspect
        (ADR 0021).
        """
        raw_text = output.model_dump_json()
        usage = acc.finalize(model=model, pricing=self._pricing_table())
        final = Message(role=MessageRole.ASSISTANT, content=raw_text)
        return ProviderResponse[T_Output](
            output=output,
            raw_text=raw_text,
            usage=usage,
            model=model,
            provider=_PROVIDER_NAME,
            conversation=[*trace, final],
            tool_calls=calls_made,
        )

    # --- vendor call + retry ----------------------------------------------

    async def _create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
        max_tokens: int,
        acc: _UsageAccumulator,
    ) -> Any:  # noqa: ANN401 -- anthropic Message response; SDK typing adds no value here
        """One vendor call wrapped in transient-failure retry; accumulates usage.

        Transient conditions (timeout, connection error, 429, 5xx) retry with
        exponential backoff + jitter per ``self._transient`` (``pipeline.md``
        §3.7); on exhaustion raises ``TransientFailure``. Non-retryable vendor
        errors (auth, 400/404, quota) raise ``HardFailure`` (§6.3).
        """
        last_exc: BaseException | None = None
        for attempt in range(1, self._transient.max_attempts + 1):
            try:
                response = await self._call_once(
                    model=model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                )
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                last_exc = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code == 429 or exc.status_code >= 500:
                    last_exc = exc
                else:
                    # Opt-in diagnostic (off unless CYBERLAB_GEN_DEBUG_TOOL_LOOP is
                    # set): on a non-retryable 4xx, dump roles + tool ids per message
                    # so a malformed message array is visible without an API round-trip
                    # (ADR 0031). The structural fix means it should no longer fire.
                    _dump_message_array_on_error(messages, model=model, exc=exc)
                    raise self._with_usage(
                        HardFailure(f"Anthropic call failed ({exc.status_code}): {exc}", cause=exc),
                        model=model,
                        acc=acc,
                    ) from exc
            except anthropic.AnthropicError as exc:
                raise self._with_usage(
                    HardFailure(f"Anthropic call failed: {exc}", cause=exc),
                    model=model,
                    acc=acc,
                ) from exc
            else:
                acc.add(getattr(response, "usage", None))
                return response

            if attempt < self._transient.max_attempts:
                await asyncio.sleep(self._backoff(attempt))
                logger.warning(
                    "anthropic transient failure on attempt %d/%d (model=%s): %s",
                    attempt,
                    self._transient.max_attempts,
                    model,
                    last_exc,
                )
        raise self._with_usage(
            TransientFailure(
                f"Anthropic call failed after {self._transient.max_attempts} attempts "
                f"(model={model!r})",
                cause=last_exc,
            ),
            model=model,
            acc=acc,
        ) from last_exc

    async def _call_once(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
        max_tokens: int,
    ) -> Any:  # noqa: ANN401 -- anthropic Message response
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        if system:
            kwargs["system"] = system
        return await self._aclient.messages.create(**kwargs)

    def _backoff(self, attempt: int) -> float:
        base = self._transient.base_delay_seconds * (
            self._transient.backoff_factor ** (attempt - 1)
        )
        if self._transient.jitter_fraction <= 0 or base <= 0:
            return base
        # secrets.randbelow for a deterministic-typed jitter without importing `random`.
        jitter = base * self._transient.jitter_fraction * (secrets.randbelow(2001) / 1000.0 - 1.0)
        return max(0.0, base + jitter)

    # --- helpers -----------------------------------------------------------

    @property
    def _aclient(self) -> Any:  # noqa: ANN401 -- anthropic.AsyncAnthropic
        if self._client is None:
            try:
                self._client = anthropic.AsyncAnthropic()
            except anthropic.AnthropicError as exc:
                raise HardFailure(
                    "Anthropic client could not be initialized (is ANTHROPIC_API_KEY set?)",
                    cause=exc,
                ) from exc
        return self._client

    def _pricing_table(self) -> PricingTable:
        if self._pricing is None:
            self._pricing = load_pricing_table()
        return self._pricing

    def _with_usage[E: ProviderError](self, exc: E, *, model: str, acc: _UsageAccumulator) -> E:
        """Attach the accumulated vendor-billed usage to a failing ``ProviderError``.

        A structured-output call that ultimately raises (a truncated/malformed emit
        that exhausted its retries, a tool loop that never converged, a transient
        condition that gave up mid-loop) was still billed for every vendor call in
        ``acc``. Finalizing that onto the exception lets the cost-recording layer
        record the spend even though no ``ProviderResponse`` is returned, keeping the
        ledger and the cost cap honest (ADR 0033; the accounting bug where billed-
        but-raised calls were invisible). Best-effort: skips when nothing was billed
        or usage is already attached, and swallows a finalize failure (missing
        pricing) so accounting never masks the original error. Returns ``exc`` so
        callers can ``raise self._with_usage(SomeError(...), ...)``.
        """
        if exc.usage is None and acc.calls > 0:
            try:
                exc.usage = acc.finalize(model=model, pricing=self._pricing_table())
                exc.model = model
            except HardFailure:
                logger.warning(
                    "could not finalize billed usage for a failed call (model=%s); "
                    "the ledger will under-report this spend",
                    model,
                )
        return exc

    def _resolve_model(self, capability: CapabilityHint) -> str:
        entries = self._rankings.by_capability.get(capability, [])
        for entry in entries:
            if entry.provider == _PROVIDER_NAME:
                return entry.model
        raise HardFailure(
            f"capability {capability.value!r} has no anthropic entry in model_rankings.yaml"
        )


def _translate_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split ``Message`` list into (system text, Anthropic-format messages).

    SYSTEM turns are concatenated into the top-level ``system`` string (Anthropic
    keeps system out of the messages array). USER/ASSISTANT/TOOL turns map to the
    messages array; TOOL turns become a ``tool_result`` block inside a user
    message (``provider-interface.md`` §4.1 content-shape contract).
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role is MessageRole.SYSTEM:
            system_parts.append(msg.content)
        elif msg.role is MessageRole.USER:
            out.append({"role": "user", "content": msg.content})
        elif msg.role is MessageRole.ASSISTANT:
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": call.tool_name,
                        "input": call.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks or msg.content})
        else:  # MessageRole.TOOL
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            )
    return "\n\n".join(system_parts), out


def _emit_tool(output_schema: type[BaseModel]) -> dict[str, Any]:
    """Build the forced 'emit' tool that carries the structured output schema."""
    raw = "".join(c if c.isalnum() or c in "_-" else "_" for c in output_schema.__name__)
    name = (_EMIT_TOOL_PREFIX + raw)[:64]
    return {
        "name": name,
        "description": (
            f"Return the final result as a {output_schema.__name__}. You MUST call this "
            "tool exactly once, with arguments matching the schema, to finish."
        ),
        "input_schema": output_schema.model_json_schema(),
    }


async def _execute_tool(tool_executor: ToolExecutor, call: ToolCall) -> tuple[str, bool]:
    """Run one tool call, turning an executor *exception* into an error result (ADR 0029).

    Anthropic requires a ``tool_result`` for every ``tool_use``, including tools
    whose execution failed — a dropped block triggers the 400 that aborted the
    run. An executor that raises therefore becomes an ``is_error`` result here
    rather than killing the loop; the model can then recover within its budget.
    Returns ``(content, is_error)``.
    """
    try:
        result = await tool_executor.execute(call)
    # Broad by design: any executor failure must still yield a tool_result (ADR 0029).
    except Exception as exc:
        logger.warning(
            "tool %s (call %s) raised during execution: %s", call.tool_name, call.call_id, exc
        )
        return f"tool execution failed: {exc}", True
    return result.content, result.is_error


def _translate_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _is_tool_use(block: object) -> bool:
    return getattr(block, "type", None) == "tool_use"


def _block_name(block: object) -> str:
    return str(getattr(block, "name", ""))


def _block_id(block: object) -> str:
    return str(getattr(block, "id", ""))


def _block_input(block: object) -> dict[str, Any]:
    value = getattr(block, "input", None)
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return {}


def _dbg_field(block: object, name: str) -> Any:  # noqa: ANN401 -- reads a dict-or-SDK-block field
    if isinstance(block, dict):
        return cast("dict[str, Any]", block).get(name)
    return getattr(block, name, None)


def _debug_summarize_messages(messages: list[dict[str, Any]]) -> str:
    """One readable line per message: index, role, tool_use ids, tool_result ids.

    Roles and ids only — never message content — so it is safe to print and
    leaks nothing. Assistant turns whose ``tool_use`` ids are not all answered by
    a ``tool_result`` in the immediately following message are flagged ``<<<
    MALFORMED`` so the offending message is obvious without the API round-trip.
    """
    rows: list[tuple[int, object, list[object], list[object], int]] = []
    for i, msg in enumerate(messages):
        content = _dbg_field(msg, "content")
        tool_use_ids: list[object] = []
        tool_result_ids: list[object] = []
        text_blocks = 0
        if isinstance(content, list):
            for block in cast("list[object]", content):
                kind = _dbg_field(block, "type")
                if kind == "tool_use":
                    tool_use_ids.append(_dbg_field(block, "id"))
                elif kind == "tool_result":
                    tool_result_ids.append(_dbg_field(block, "tool_use_id"))
                elif kind == "text":
                    text_blocks += 1
        elif isinstance(content, str):
            text_blocks = 1
        rows.append((i, _dbg_field(msg, "role"), tool_use_ids, tool_result_ids, text_blocks))

    lines: list[str] = []
    for idx, (i, role, tool_use_ids, tool_result_ids, text_blocks) in enumerate(rows):
        note = ""
        if role == "assistant" and tool_use_ids:
            answered: set[object] = set(rows[idx + 1][3]) if idx + 1 < len(rows) else set()
            missing = [tid for tid in tool_use_ids if tid not in answered]
            if missing:
                note = f"  <<< MALFORMED: no tool_result for {missing} in the next message"
        lines.append(
            f"  [{i}] role={role} text_blocks={text_blocks} "
            f"tool_use={tool_use_ids} tool_result={tool_result_ids}{note}"
        )
    return "\n".join(lines)


def _dump_message_array_on_error(
    messages: list[dict[str, Any]], *, model: str, exc: object
) -> None:
    """Dump the message-array summary to stderr on a 4xx — only when debug is enabled.

    Gated behind the :data:`_DEBUG_TOOL_LOOP_ENV` env var so normal runs are quiet
    (ADR 0031). Left in (off by default) as a one-flag diagnostic should a future
    malformed-array regression appear; the structural fix means it should not fire.
    """
    if not os.environ.get(_DEBUG_TOOL_LOOP_ENV):
        return
    status = getattr(exc, "status_code", "?")
    summary = _debug_summarize_messages(messages)
    banner = (
        f"\n=== ANTHROPIC {status} TOOL-LOOP DEBUG (model={model}) ===\n"
        f"error: {exc}\n"
        f"message array ({len(messages)} messages; roles + tool ids only):\n"
        f"{summary}\n"
        f"=== end tool-loop debug ===\n"
    )
    print(banner, file=sys.stderr, flush=True)  # noqa: T201 -- opt-in stderr diagnostic
    logger.error("anthropic %s tool-loop failure; message array:\n%s", status, summary)


def _dump_emit_on_validation_error(
    emit_input: dict[str, Any],
    *,
    schema_name: str,
    exc: PydanticValidationError,
    stop_reason: str | None = None,
    output_tokens: int = 0,
    max_tokens: int = 0,
) -> None:
    """Diagnose an emit that failed schema validation (ADR 0032 Symptom-2 follow-up).

    Two outputs, by audience:

    * **Always** (no env var) — a concise, unmissable ``WARNING`` verdict on the
      load-bearing question: was the emit *truncated* (the response stopped at
      ``max_tokens`` mid-emit) or *complete-but-schema-invalid* (a genuine content
      problem)? ``stop_reason == "max_tokens"`` is the authoritative truncation
      signal; ``output_tokens`` vs ``max_tokens`` corroborates. This is the line a
      maintainer needs and it costs nothing to show on every run.
    * **Opt-in** (:data:`_DEBUG_EMIT_ENV`) — the model's raw emitted arguments dumped
      to stderr. Unlike :func:`_dump_message_array_on_error` (ids only) this prints
      *content* — the whole point is to see *what* the model produced (e.g. an
      ``in_scope`` AttackSpec that omitted ``chain``). Large, so it is off by default.
    """
    truncated = stop_reason == "max_tokens" or (max_tokens > 0 and output_tokens >= max_tokens)
    if truncated:
        logger.warning(
            "emit FAILED validation AND the response stopped at max_tokens "
            "(stop_reason=%s, output_tokens=%s, max_tokens=%s): the emitted %s was "
            "TRUNCATED mid-emit, not a deliberate omission -- raise the caller's "
            "max_tokens. Parse error: %s",
            stop_reason,
            output_tokens,
            max_tokens,
            schema_name,
            _short_error(exc),
        )
        # ADR 0035: persist the raw partial content (gated on _EMIT_DUMP_DIR_ENV) so a
        # maintainer can finally READ a truncated emit. Best-effort; never changes the
        # ADR-0033 halt that follows.
        _write_truncation_dump(
            emit_input,
            schema_name=schema_name,
            stop_reason=stop_reason,
            output_tokens=output_tokens,
            max_tokens=max_tokens,
            parse_error=_short_error(exc),
        )
    else:
        logger.warning(
            "emit FAILED validation and the response ended normally "
            "(stop_reason=%s, output_tokens=%s/%s): the emitted %s was COMPLETE but "
            "schema-invalid (a content problem, not truncation). Parse error: %s",
            stop_reason,
            output_tokens,
            max_tokens,
            schema_name,
            _short_error(exc),
        )
    if not os.environ.get(_DEBUG_EMIT_ENV):
        return
    try:
        rendered = json.dumps(emit_input, indent=2, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive; emit_input is plain JSON
        rendered = repr(emit_input)
    if len(rendered) > 16000:
        rendered = f"{rendered[:16000]}\n... (dump truncated for display; emit was longer)"
    banner = (
        f"\n=== EMIT VALIDATION FAILURE ({schema_name}) ===\n"
        f"stop_reason={stop_reason} output_tokens={output_tokens} max_tokens={max_tokens}\n"
        f"error: {_short_error(exc)}\n"
        f"emitted arguments (raw, as the model produced them):\n"
        f"{rendered}\n"
        f"=== end emit dump ===\n"
    )
    print(banner, file=sys.stderr, flush=True)  # noqa: T201 -- opt-in stderr diagnostic
    logger.error("emit validation failure (%s): %s", schema_name, _short_error(exc))


def _write_truncation_dump(
    emit_input: dict[str, Any],
    *,
    schema_name: str,
    stop_reason: str | None,
    output_tokens: int,
    max_tokens: int,
    parse_error: str,
) -> None:
    """Persist a truncated emit's RAW partial content for inspection (ADR 0035).

    Gated behind :data:`_EMIT_DUMP_DIR_ENV` (a directory path) — a no-op when unset,
    so normal runs write nothing. Writes ``<dir>/<schema>-truncated.json``: a
    ``_truncation_dump`` header (schema, stop_reason, output/max tokens, and the parse
    error that marks roughly where the emit cut off) plus ``emitted_arguments`` — the
    raw, incomplete-and-schema-invalid tool-call arguments the model produced. The
    point is to judge whether the spec is *tight-but-large* or *bloated* (verbose
    descriptions, over-long excerpts), which decides whether the long-blog fix (P4)
    needs prompt tightening or chunked-emit alone.

    The provider does not know the blog id; identify the blog from the ``source``
    block inside ``emitted_arguments``. Re-running overwrites the same-named file
    (latest wins) — fine for the single-``--blog`` diagnostic this exists for.

    **Additive and best-effort:** a write failure is logged and swallowed, so this
    never masks or changes the ADR-0033 truncation halt that immediately follows.
    """
    dump_dir = os.environ.get(_EMIT_DUMP_DIR_ENV)
    if not dump_dir:
        return
    payload = {
        "_truncation_dump": {
            "schema": schema_name,
            "stop_reason": stop_reason,
            "output_tokens": output_tokens,
            "max_tokens": max_tokens,
            "parse_error": parse_error,
            "note": (
                "RAW partial emit: the model's tool-call arguments, cut off at max_tokens "
                "before it finished. Incomplete and schema-invalid BY DESIGN. Identify the "
                "blog from the 'source' block in emitted_arguments below."
            ),
        },
        "emitted_arguments": emit_input,
    }
    try:
        target_dir = Path(dump_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"{schema_name}-truncated.json"
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except OSError as os_exc:
        logger.warning(
            "could not write truncated-emit dump to %r (%s); the run still halts normally",
            dump_dir,
            os_exc,
        )
        return
    logger.warning(
        "wrote the RAW partial %s emit (%d output tokens, truncated) to %s",
        schema_name,
        output_tokens,
        out,
    )


def _stop_reason(response: object) -> str | None:
    """The vendor response's ``stop_reason`` (``"max_tokens"`` ⇒ truncated)."""
    value = getattr(response, "stop_reason", None)
    return str(value) if value is not None else None


def _is_truncated(response: object) -> bool:
    """``True`` when the vendor stopped at the output-token limit (ADR 0033).

    ``stop_reason == "max_tokens"`` is the authoritative truncation signal: an emit
    that failed schema validation *and* stopped here was cut off mid-emit, not
    deliberately malformed. The caller halts on it (non-retryable) rather than
    regenerating — a regeneration produces the same oversized output and truncates
    again at the same budget. Deliberately keyed only on ``stop_reason`` (not the
    ``output_tokens >= max_tokens`` heuristic the diagnostic uses) so a complete-but-
    invalid emit that merely happens to be near the limit is never mis-halted.
    """
    return _stop_reason(response) == "max_tokens"


def _emit_truncated(*, schema_name: str, max_tokens: int, output_tokens: int) -> EmitTruncated:
    """Build the :class:`EmitTruncated` halt for a cut-off emit (ADR 0033).

    The message is the run's ``halt_reason`` (the eval/CLI surface ``str(exc)``): it
    must name the condition (truncated at the token limit) and the only remedies that
    help (raise ``max_tokens`` or shorten the input), since retrying cannot.
    """
    return EmitTruncated(
        f"the {schema_name} emit was truncated at the {max_tokens}-token output limit "
        f"(stop_reason=max_tokens, output_tokens={output_tokens}): the structured output was "
        f"cut off mid-emit and cannot validate against the schema. Retrying regenerates the same "
        f"oversized output and truncates again at the same budget — raise max_tokens (up to the "
        f"non-streaming ceiling) or shorten the input."
    )


def _text_of(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


def _short_error(exc: PydanticValidationError) -> str:
    errors = exc.errors()
    head = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors[:3])
    return head if len(errors) <= 3 else f"{head}; (+{len(errors) - 3} more)"
