"""Anthropic provider — a thin adapter over a pydantic-ai ``Agent`` (ADR 0036).

Architectural source: ``provider-interface.md`` §4 (call surface), §6 (error
semantics), §3.4 (capability resolution); ``architecture.md`` §1.5 / ``pipeline.md``
§3.1 ("Pydantic AI for typed agents"). ADR 0036 (this migration), ADR 0018
(two-layer structural-retry budget), ADR 0033 (truncation halt + billed-on-raise).

What this module does (and what it deliberately does not):

- **The agent runtime is pydantic-ai.** Each call builds a pydantic-ai ``Agent``
  with the declared ``output_schema`` as ``output_type`` (forced-tool structured
  output by default), the supplied ``ToolDefinition`` set as ``Tool.from_schema``
  tools whose implementation calls back into our ``ToolExecutor``, and drives it
  via ``agent.iter`` so token usage is captured on success **and** on failure
  (ADR 0036 spike: ``run.usage`` survives a raise; the exception does not carry it).
- **Model resolution.** The ``Provider`` ABC hands the adapter a ``CapabilityHint``,
  not a model id. The adapter resolves it the same way ``ProviderRegistry`` does:
  the first ``anthropic`` entry for that capability in the bundled
  ``model_rankings.yaml``. The resolved id is what we price and report; the actual
  pydantic-ai model is built from it (or an injected test model is used).
- **Cost stays ours.** Token counts come from pydantic-ai's ``RunUsage``; the USD
  figure is computed by ``cost_ledger.compute_cost`` (Decimal, our ``pricing.yaml``)
  — not pydantic-ai's float ``genai-prices`` — so by-agent/by-model rollups stay
  exact. ``ProviderResponse.usage`` carries the per-invocation total.
- **Retry split (ADR 0018).** pydantic-ai's ``retries={'output': N}`` is the
  provider-internal malformed-output budget; the *stage-level* structural retry,
  the ADR-0032 no-progress bail, and the ADR-0033 ``EmitTruncated`` passthrough all
  remain at the call surface (``agents.call_surface``).
- **No sampling params** (temperature/top_p/top_k) are sent — deliberate, per the
  ``Provider`` ABC docstring and ``provider-interface.md`` §4.1.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import anthropic
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelAPIError,
    ModelHTTPError,
    ModelRetry,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider as PydAnthropicProvider
from pydantic_ai.tools import Tool
from pydantic_ai.usage import UsageLimits

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

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "anthropic"

#: Default output cap when a caller passes ``max_tokens=None``. The Anthropic API
#: requires ``max_tokens``; agents that need more (the Extractor's AttackSpec)
#: pass an explicit value.
DEFAULT_MAX_TOKENS = 4096

#: Provider-internal malformed-output retry budget (pydantic-ai ``retries={'output': N}``).
#: This is the within-call re-prompt budget that ADR 0018 places *below* the call
#: surface's stage-level structural retry; 2 matches the prior adapter
#: (``MALFORMED_OUTPUT_RETRIES``).
DEFAULT_OUTPUT_RETRIES = 2

#: Transient HTTP status codes (429 + 5xx) the injected client retries; on
#: exhaustion they surface as ``ModelHTTPError`` and map to ``TransientFailure``.
_TRANSIENT_STATUS_MIN_SERVER_ERROR = 500
_RATE_LIMIT_STATUS = 429


class AnthropicProvider(Provider):
    """Anthropic adapter implementing the ``Provider`` call surface via pydantic-ai.

    The model backend is injectable for offline tests: pass ``model=`` a pydantic-ai
    ``Model`` (e.g. ``FunctionModel``/``TestModel``) to drive the agent without any
    network or API key. The live path constructs a real ``anthropic.AsyncAnthropic``
    lazily (reading ``ANTHROPIC_API_KEY`` from the environment) and wraps it in
    pydantic-ai's ``AnthropicModel``. An ``AsyncAnthropic`` may also be injected via
    ``client=`` (e.g. a VCR-backed client for the live cassette test).
    """

    def __init__(
        self,
        *,
        client: Any | None = None,  # noqa: ANN401 -- anthropic.AsyncAnthropic | None; SDK is untyped enough that a precise type buys nothing
        model: Model | None = None,
        output_retries: int = DEFAULT_OUTPUT_RETRIES,
    ) -> None:
        self._client = client
        self._injected_model = model
        self._output_retries = output_retries
        self._rankings = load_model_rankings()
        self._pricing: PricingTable | None = None
        self._aclient_cache: Any | None = None

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
        return await self._invoke(
            messages,
            output_schema=output_schema,
            capability=capability,
            tools=[],
            tool_executor=None,
            max_tokens=max_tokens,
            request_limit=None,
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
        # Each loop iteration is one model request; allow max_iterations tool turns
        # plus the final emit turn. Exceeding it raises UsageLimitExceeded, which we
        # map to ToolLoopError (provider-interface.md §6.4).
        return await self._invoke(
            messages,
            output_schema=output_schema,
            capability=capability,
            tools=tools,
            tool_executor=tool_executor,
            max_tokens=max_tokens,
            request_limit=max_iterations + 1,
        )

    # --- core -------------------------------------------------------------

    async def _invoke[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor | None,
        max_tokens: int | None,
        request_limit: int | None,
    ) -> ProviderResponse[T_Output]:
        model_id = self._resolve_model(capability)
        pyd_model = self._pydantic_model(model_id)
        instructions, history, prompt = _translate_messages(messages)
        pyd_tools = [_make_tool(t, tool_executor) for t in tools if tool_executor is not None]

        agent: Agent[None, T_Output] = Agent(
            pyd_model,
            output_type=output_schema,
            instructions=instructions or None,
            tools=pyd_tools,
            retries={"output": self._output_retries},
            model_settings={"max_tokens": max_tokens or DEFAULT_MAX_TOKENS},
        )
        usage_limits = UsageLimits(request_limit=request_limit) if request_limit else None

        run_usage: RunUsage | None = None
        try:
            async with agent.iter(
                prompt, message_history=history or None, usage_limits=usage_limits
            ) as run:
                try:
                    async for _node in run:
                        pass
                finally:
                    # RunUsage lives in graph state, so it is readable here even when
                    # the iteration raised (ADR 0036 spike). Must be read inside the
                    # async-with scope, before the exception leaves it.
                    run_usage = run.usage
                result = run.result
                messages_out = run.all_messages()
        except (
            IncompleteToolCall,
            UsageLimitExceeded,
            UnexpectedModelBehavior,
            ModelHTTPError,
            ModelAPIError,
            UserError,
        ) as exc:
            raise self._map_error(exc, model_id=model_id, run_usage=run_usage) from exc

        if result is None:  # pragma: no cover - iter always reaches End on success
            raise MalformedOutput(
                "pydantic-ai run produced no result",
                usage=self._safe_usage(run_usage, model_id),
                model=model_id,
            )

        usage = self._finalize_usage(run_usage, model_id)
        finish_reason = _final_finish_reason(messages_out)
        if finish_reason == "length":
            # A truncation whose partial output happened to parse validly is returned
            # silently by pydantic-ai; halt it explicitly to preserve the ADR-0033
            # contract (retrying regenerates the same oversized output — only burns money).
            raise EmitTruncated(
                f"emit truncated at the output-token limit (finish_reason='length', "
                f"max_tokens={max_tokens or DEFAULT_MAX_TOKENS}); raise max_tokens or "
                f"shorten the input — re-running will truncate again at the same budget",
                usage=usage,
                model=model_id,
            )

        output = result.output
        raw_text = output.model_dump_json()
        conversation = _translate_back(messages_out, raw_text)
        tool_calls = _collect_tool_calls(messages_out)
        return ProviderResponse[T_Output](
            output=output,
            raw_text=raw_text,
            usage=usage,
            model=model_id,
            provider=_PROVIDER_NAME,
            conversation=conversation,
            tool_calls=tool_calls,
        )

    # --- model + pricing --------------------------------------------------

    def _resolve_model(self, capability: CapabilityHint) -> str:
        """Resolve a capability to the first ``anthropic`` model id in the rankings.

        The adapter is only invoked once ``ProviderRegistry`` resolved the capability
        to an anthropic model, so the first anthropic entry agrees by construction
        (provider-interface.md §3.4). Raises ``HardFailure`` if none exists.
        """
        entries = self._rankings.by_capability.get(capability, [])
        for entry in entries:
            if entry.provider == _PROVIDER_NAME:
                return entry.model
        raise HardFailure(
            f"No anthropic model ranked for capability {capability.value!r}; "
            f"the adapter was invoked for a capability it cannot serve."
        )

    def _pydantic_model(self, model_id: str) -> Model:
        if self._injected_model is not None:
            return self._injected_model
        return AnthropicModel(
            model_id, provider=PydAnthropicProvider(anthropic_client=self._aclient)
        )

    @property
    def _aclient(self) -> Any:  # noqa: ANN401 -- anthropic.AsyncAnthropic; SDK typing buys nothing
        if self._client is not None:
            return self._client
        if self._aclient_cache is None:
            try:
                self._aclient_cache = anthropic.AsyncAnthropic()
            except Exception as exc:
                # Surface any client-init failure (e.g. missing key) as a HardFailure
                # with its cause — never swallowed.
                raise HardFailure(
                    "failed to construct the Anthropic client (is ANTHROPIC_API_KEY set?)",
                    cause=exc,
                ) from exc
        return self._aclient_cache

    def _pricing_table(self) -> PricingTable:
        if self._pricing is None:
            self._pricing = load_pricing_table()
        return self._pricing

    def _finalize_usage(self, run_usage: RunUsage | None, model_id: str) -> TokenUsage:
        """Build a costed ``TokenUsage`` from pydantic-ai's ``RunUsage``.

        Raises ``HardFailure`` when ``model_id`` is absent from the pricing table: a
        billed call whose cost we cannot compute would silently report zero and
        corrupt budget tracking (provider-interface.md §5).
        """
        base = _run_usage_to_token_usage(run_usage)
        try:
            cost = compute_cost(
                self._pricing_table(), provider=_PROVIDER_NAME, model=model_id, usage=base
            )
        except KeyError as exc:
            raise HardFailure(
                f"No pricing entry for the resolved model {model_id!r}; refusing to report "
                f"zero cost for a billed call. Add it to cyberlab_gen/providers/pricing.yaml "
                f"or fix the model_rankings.yaml entry.",
                cause=exc,
            ) from exc
        return base.model_copy(update={"cost_usd": cost})

    def _safe_usage(self, run_usage: RunUsage | None, model_id: str) -> TokenUsage | None:
        """Cost the usage for a *raised* call, never masking the original failure.

        Attaching billed usage to a raised ``ProviderError`` keeps the cost cap honest
        (ADR 0033). If pricing is missing we log and attach nothing rather than letting
        a finalize ``HardFailure`` replace the real error.
        """
        if run_usage is None:
            return None
        try:
            return self._finalize_usage(run_usage, model_id)
        except HardFailure:
            logger.warning(
                "could not finalize billed usage for a failed call (model=%s); the ledger "
                "will under-report this spend",
                model_id,
                exc_info=True,
            )
            return None

    def _map_error(
        self, exc: Exception, *, model_id: str, run_usage: RunUsage | None
    ) -> ProviderError:
        """Translate a pydantic-ai exception into the project error hierarchy (ADR 0036)."""
        usage = self._safe_usage(run_usage, model_id)
        if isinstance(exc, IncompleteToolCall):
            return EmitTruncated(
                f"emit truncated at the output-token limit while generating a tool call "
                f"({exc}); raise max_tokens or shorten the input — re-running will truncate "
                f"again at the same budget",
                usage=usage,
                model=model_id,
                cause=exc,
            )
        if isinstance(exc, UsageLimitExceeded):
            return ToolLoopError(
                f"tool-use loop exceeded its request budget without a final structured "
                f"output ({exc})",
                usage=usage,
                model=model_id,
                cause=exc,
            )
        if isinstance(exc, ModelHTTPError):
            status = exc.status_code
            if status == _RATE_LIMIT_STATUS or status >= _TRANSIENT_STATUS_MIN_SERVER_ERROR:
                return TransientFailure(
                    f"transient anthropic API error (status {status})",
                    usage=usage,
                    model=model_id,
                    cause=exc,
                )
            return HardFailure(
                f"non-retryable anthropic API error (status {status})",
                usage=usage,
                model=model_id,
                cause=exc,
            )
        if isinstance(exc, UserError):
            return HardFailure(
                f"anthropic adapter misuse: {exc}", usage=usage, model=model_id, cause=exc
            )
        if isinstance(exc, ModelAPIError):
            # Connection/timeout-class model errors with no HTTP status: transient.
            return TransientFailure(
                f"transient anthropic API error: {exc}", usage=usage, model=model_id, cause=exc
            )
        # Remaining UnexpectedModelBehavior: output-retry budget exhausted, etc.
        return MalformedOutput(
            f"model did not produce a schema-valid output ({exc})",
            usage=usage,
            model=model_id,
            cause=exc,
        )


# --- module helpers -------------------------------------------------------


def _make_tool(td: ToolDefinition, executor: ToolExecutor | None) -> Tool[None]:
    """Wrap a ``ToolDefinition`` as a pydantic-ai tool backed by our ``ToolExecutor``.

    ``Tool.from_schema`` uses the pre-built JSON schema verbatim (it skips Pydantic
    arg validation), so the model-facing tool matches the prior forced-tool surface.
    The implementation forwards to ``ToolExecutor.execute``; an error result is raised
    as ``ModelRetry`` so the model sees it and can recover (the ADR-0029 "answer every
    tool call" contract is handled by pydantic-ai's loop).
    """
    assert executor is not None  # guarded by the caller's filter

    _counter = {"n": 0}

    async def _call(**kwargs: Any) -> str:  # noqa: ANN401 -- args validated by the model-facing schema, not here
        _counter["n"] += 1
        call = ToolCall(
            call_id=f"{td.name}-{_counter['n']}", tool_name=td.name, arguments=dict(kwargs)
        )
        result = await executor.execute(call)
        if result.is_error:
            raise ModelRetry(result.content)
        return result.content

    return Tool.from_schema(
        _call, name=td.name, description=td.description, json_schema=td.input_schema
    )


def _translate_messages(messages: list[Message]) -> tuple[str, list[ModelMessage], str]:
    """Split our ``Message`` list into (instructions, history, final-user-prompt).

    System turns concatenate into the agent ``instructions``. The remaining turns
    become pydantic-ai message history, except the last user turn which is the run
    prompt. For the common ``[SYSTEM, USER]`` shape this yields
    ``(system_text, [], user_text)``.
    """
    system_parts = [m.content for m in messages if m.role is MessageRole.SYSTEM and m.content]
    instructions = "\n\n".join(system_parts)
    non_system = [m for m in messages if m.role is not MessageRole.SYSTEM]

    if not non_system:
        return instructions, [], ""

    prompt = ""
    tail = non_system
    if non_system[-1].role is MessageRole.USER:
        prompt = non_system[-1].content
        tail = non_system[:-1]

    history: list[ModelMessage] = []
    for msg in tail:
        if msg.role is MessageRole.USER:
            history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
        elif msg.role is MessageRole.ASSISTANT:
            parts: list[Any] = []
            if msg.content:
                parts.append(TextPart(content=msg.content))
            for tc in msg.tool_calls:
                parts.append(
                    ToolCallPart(tool_name=tc.tool_name, args=tc.arguments, tool_call_id=tc.call_id)
                )
            history.append(ModelResponse(parts=parts))
        elif msg.role is MessageRole.TOOL:
            history.append(
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="",
                            content=msg.content,
                            tool_call_id=msg.tool_call_id or "",
                        )
                    ]
                )
            )
    return instructions, history, prompt


def _final_finish_reason(messages_out: list[ModelMessage]) -> str | None:
    for msg in reversed(messages_out):
        if isinstance(msg, ModelResponse):
            return msg.finish_reason
    return None


def _translate_back(messages_out: list[ModelMessage], raw_text: str) -> list[Message]:
    """Reconstruct our ``Message`` conversation from pydantic-ai's message history.

    The final assistant turn's content is set to ``raw_text`` (the serialized output)
    so callers can read the shipped result off ``conversation[-1]`` regardless of how
    pydantic-ai represented the final emit (an output-tool call internally).
    """
    conversation: list[Message] = []
    for msg in messages_out:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    conversation.append(
                        Message(role=MessageRole.SYSTEM, content=_as_text(part.content))
                    )
                elif isinstance(part, UserPromptPart):
                    conversation.append(
                        Message(role=MessageRole.USER, content=_as_text(part.content))
                    )
                elif isinstance(part, ToolReturnPart):
                    conversation.append(
                        Message(
                            role=MessageRole.TOOL,
                            content=_as_text(part.content),
                            tool_call_id=part.tool_call_id,
                        )
                    )
        else:  # ModelResponse (ModelMessage is exhaustively request | response)
            text_parts = [p.content for p in msg.parts if isinstance(p, TextPart)]
            tool_calls = [
                ToolCall(
                    call_id=p.tool_call_id,
                    tool_name=p.tool_name,
                    arguments=_args_as_dict(p),
                )
                for p in msg.parts
                if isinstance(p, ToolCallPart)
            ]
            if tool_calls:
                conversation.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content="".join(text_parts),
                        tool_calls=tool_calls,
                    )
                )
    conversation.append(Message(role=MessageRole.ASSISTANT, content=raw_text))
    return conversation


def _collect_tool_calls(messages_out: list[ModelMessage]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for msg in messages_out:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(
                        ToolCall(
                            call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            arguments=_args_as_dict(part),
                        )
                    )
    return calls


def _args_as_dict(part: ToolCallPart) -> dict[str, Any]:
    try:
        args = part.args_as_dict()
    except (ValueError, TypeError):
        return {}
    return dict(args)


def _as_text(content: object) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def _run_usage_to_token_usage(run_usage: RunUsage | None) -> TokenUsage:
    if run_usage is None:
        return TokenUsage(input_tokens=0, output_tokens=0, cost_usd=Decimal("0"))
    return TokenUsage(
        input_tokens=run_usage.input_tokens,
        output_tokens=run_usage.output_tokens,
        cache_read_tokens=run_usage.cache_read_tokens,
        cache_write_tokens=run_usage.cache_write_tokens,
        cost_usd=Decimal("0"),
    )
