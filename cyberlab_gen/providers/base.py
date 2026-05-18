"""Provider call surface — the `Provider` ABC and its supporting types.

Architectural source: ``provider-interface.md`` §4 (capabilities and call
surface), §6 (errors). This module ships in Phase 0 Task 5a; the concrete
adapter bodies (``AnthropicProvider``) land in Phase 1.

Type discipline (per ADR 0004 case 3 + ADR 0008 precedent):

- The Pydantic types below are in-memory typed wrappers (not YAML-bound
  artifacts), so they use bare ``BaseModel + ConfigDict(extra="forbid",
  frozen=True)`` rather than ``ArtifactModel``. ``frozen=True`` matches
  the construct-once usage: ``ProviderResponse`` and ``Message`` are
  assembled by the provider and never mutated by callers.
- ``ProviderResponse`` uses PEP 695 generic syntax with a ``BaseModel``
  bound (``coding-conventions.md`` §4.3); ``arbitrary_types_allowed=True``
  from ``provider-interface.md`` §4.1 is dropped — Pydantic v2 does not
  need it for ``BaseModel``-bound generics.
- The brief audit found ``provider-interface.md`` §4.1 uses
  ``Generic[T_Output]``; implementation deviates to PEP 695 per the
  conventions doc. Recorded as doc-improvement note.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapabilityHint(StrEnum):
    """Capability requested by an agent; resolved to (provider, model) by ``ProviderRegistry``.

    The canonical definition (``provider-interface.md`` §4.1). The §3.1
    block in the same doc is illustrative only and must stay in sync.
    """

    HIGH_QUALITY_REASONING = "high_quality_reasoning"
    FAST_CHEAP_STRUCTURED_OUTPUT = "fast_cheap_structured_output"
    LONG_CONTEXT_EXTRACTION = "long_context_extraction"


class AgentLabel(StrEnum):
    """Closed set of system agents.

    Used for cost-ledger attribution and by-agent reporting. Free-form
    strings would silently break rollups on a typo (``agents.md`` §5.2).
    Values are snake_case forms of the agent names in ``agents.md`` §5.2.
    """

    EXTRACTOR = "extractor"
    EXTRACTOR_JURY = "extractor_jury"
    PLANNER = "planner"
    PLANNER_JURY = "planner_jury"
    PER_PHASE_GENERATOR = "per_phase_generator"
    LAB_LEVEL_GENERATOR = "lab_level_generator"
    CLEANUP_GENERATOR = "cleanup_generator"
    DOCS_GENERATOR = "docs_generator"
    CRITIC = "critic"
    REPAIR_AGENT = "repair_agent"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolDefinition(BaseModel):
    """A tool the model may call during generation.

    The provider layer translates this to the vendor SDK's tool format.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    """A tool invocation the model made.

    The ``ToolExecutor`` runs it and the result flows back as a ``Message``
    with role=TOOL whose ``tool_call_id`` matches ``call_id``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Result of executing a tool call. Embedded into the next ``Message``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """A single conversation turn.

    Content shape depends on role (per ``provider-interface.md`` §4.1):

    - SYSTEM / USER: ``content`` is a non-empty string; ``tool_calls`` and
      ``tool_call_id`` are absent.
    - ASSISTANT: ``content`` carries any text the model emitted (possibly
      empty if the turn is pure tool-call); ``tool_calls`` is non-empty
      when the model invoked tools. Both populated is valid — the model
      may emit text AND tool calls in a single turn.
    - TOOL: ``content`` is the tool result's string content;
      ``tool_call_id`` references the originating assistant tool_call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list[ToolCall])
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def _role_shape(self) -> "Message":
        if self.role is MessageRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool_call_id required when role is tool")
        if self.role is not MessageRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id only valid when role is tool")
        if self.role is not MessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("tool_calls only valid on assistant messages")
        if self.role in (MessageRole.SYSTEM, MessageRole.USER) and not self.content:
            raise ValueError(f"{self.role.value} messages require non-empty content")
        return self


class TokenUsage(BaseModel):
    """Token counts and cost for one provider call.

    ``Decimal`` (not ``float``) for cost so accumulated rounding error
    across many calls does not corrupt by-agent and by-model rollups in
    the run report (``pipeline.md`` §3.5).

    Phase 0: the mock provider populates ``cost_usd=Decimal("0")``; the
    real pricing computation lands in Task 5b alongside ``pricing.yaml``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Decimal


class ProviderResponse[T_Output: BaseModel](BaseModel):
    """The structured response from a provider call.

    Generic over the agent's declared output schema. ``conversation``
    carries the full message sequence the provider sent and received
    during a ``complete_with_tools`` loop (including intermediate
    assistant tool-call messages and tool-result messages); for plain
    ``complete()`` calls it is the input messages plus the final
    assistant message.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    output: T_Output
    raw_text: str
    usage: TokenUsage
    model: str
    provider: str
    conversation: list[Message] = Field(default_factory=list[Message])
    tool_calls: list[ToolCall] = Field(default_factory=list[ToolCall])


class ToolExecutor(Protocol):
    """Executes a single tool call. Implemented by the agent layer.

    The ``Provider`` drives the tool-use loop; the ``ToolExecutor``
    implements tool semantics (registry lookup, external_lookup,
    propose_value_type, ...). See ``provider-interface.md`` §4.2.
    """

    async def execute(self, call: ToolCall) -> ToolResult: ...


class Provider(ABC):
    """The single LLM-access interface for the entire system.

    All agent code calls into a ``Provider``; no agent imports a vendor
    SDK directly. Architectural rationale: ``pipeline.md`` §3.5.

    Sampling parameters (``temperature``, ``top_p``, ``top_k``) are
    deliberately NOT surfaced — the architecture relies on capability
    hints and prompt engineering, not sampling tweaks (``provider-
    interface.md`` §4.1). Current Anthropic models error on non-default
    sampling params; omitting them from the interface keeps adapters
    clean.
    """

    @abstractmethod
    async def complete[T_Output: BaseModel](
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        """Run a single-turn structured-output completion. No tools.

        Agents that need tools call ``complete_with_tools`` instead. On
        structured-output parse failure, the implementation retries per
        ``provider-interface.md`` §6.2; final failure raises
        ``MalformedOutput``.
        """

    @abstractmethod
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
        """Run a tool-using loop ending with a structured output.

        ``max_iterations`` is a required parameter (no default) — callers
        choose a per-agent value calibrated to the agent's expected loop
        depth (``architecture.md`` §8.4 calibration entry). If the model
        produces a tool call when ``max_iterations`` is reached, the
        implementation raises ``ToolLoopError``.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in ranking and reports."""
