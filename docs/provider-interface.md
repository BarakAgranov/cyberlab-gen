# cyberlab-gen — Provider Interface

**Companion to:** `architecture.md` (hub), `pipeline.md §3.5` (provider abstraction), `pipeline.md §3.7` (provider failure handling).
**Document scope:** The exact interface contract for the `Provider` abstraction — the class signature, the input/output shapes, the capability-hint system, the cost-tracking surface, the mock provider used in tests, and the failure semantics. This is Phase 0 lock-in material per `implementation-plan.md §3.5`: changing it later means rewriting every agent.

Architectural rationale lives in `pipeline.md §3.5`. This document does not restate it; it specifies the implementation contract that flows from those decisions.

---

## 1. Position in the system

Every LLM call in cyberlab-gen — every agent invocation, every jury call, every Repair Agent exchange — goes through the `Provider` interface. Agents never import a vendor SDK directly. The provider layer is the single point where:

- Vendor SDKs are touched.
- Capability hints are resolved to concrete models.
- Structured-output parsing is enforced.
- Retries are attempted per `pipeline.md §3.7`.
- Token usage is measured and converted to cost.
- The cost ledger accumulates across the run.
- Multi-model jury diversity is sourced.

Adding a new vendor means writing one adapter and one ranking-file entry. Adding a new model means a ranking-file entry only. No agent code changes either way.

---

## 2. Module layout

```
cyberlab_gen/providers/
├── __init__.py           # public surface: Provider, ProviderRegistry, CapabilityHint, AgentLabel
├── base.py               # the Provider ABC, common dataclasses, the cost ledger
├── ranking.py            # ProviderRegistry: capability-to-model ranking loader and resolver
├── anthropic_provider.py # AnthropicProvider implementation
├── openai_provider.py    # OpenAIProvider implementation
├── mock_provider.py      # test-only provider with canned responses
├── retries.py            # retry strategy (transient errors, rate limits, malformed output)
├── errors.py             # ProviderError hierarchy
└── model_rankings.yaml   # bundled capability ranking
```

The `__init__.py` re-exports the public surface; agents import from `cyberlab_gen.providers`, never from a submodule directly.

---

## 3. Capability hints

Agents do not request models by name. They request **capability hints**, and the provider layer picks the best reachable model for the hint.

### 3.1 v1 capability hints

```python
class CapabilityHint(StrEnum):
    HIGH_QUALITY_REASONING = "high_quality_reasoning"
    FAST_CHEAP_STRUCTURED_OUTPUT = "fast_cheap_structured_output"
    LONG_CONTEXT_EXTRACTION = "long_context_extraction"
```

(This block is for orientation; the canonical definition with full type
declarations and surrounding context lives in §4.1. The two must stay in
sync — there's only one enum.)

Specified in `implementation-plan.md §3.2`. Each value maps to a ranked list of (provider, model) pairs in `model_rankings.yaml`. The provider layer walks the ranked list and picks the highest-ranked pair that the user's configured providers can reach.

When agents need additional hints (e.g., a `JURY_DIVERSE` hint for the multi-model jury), they're added to the enum and the ranking file together. Adding a hint is a deliberate change; agents must never invent ad-hoc strings.

### 3.2 Hint usage by agent role

These are guidance for the implementing agent, not part of the provider contract itself:

- Extractor → `HIGH_QUALITY_REASONING` for the main call; `LONG_CONTEXT_EXTRACTION` if the blog is unusually long.
- Planner → `HIGH_QUALITY_REASONING`.
- Per-phase Generator → `HIGH_QUALITY_REASONING` for code; structured output enforced.
- Critic → `HIGH_QUALITY_REASONING`.
- Juries → `HIGH_QUALITY_REASONING` plus diversity selection (different provider/model from the judged agent when possible, per `pipeline.md §3.5`).
- Repair Agent → `HIGH_QUALITY_REASONING`.
- Structured tool-result classification (e.g., the framework's small classification calls) → `FAST_CHEAP_STRUCTURED_OUTPUT`.

These mappings live in agent prompts and configuration, not hardcoded in the provider.

### 3.3 The ranking file: `model_rankings.yaml`

```yaml
# Bundled at cyberlab_gen/providers/model_rankings.yaml.
# Updated per release. Users may override at ~/.cyberlab-gen/model_rankings.yaml.
# Resolution: user file (if present) entirely replaces bundled; no merge.

high_quality_reasoning:
  - { provider: anthropic, model: claude-opus-4-7 }
  - { provider: anthropic, model: claude-opus-4-6 }
  - { provider: openai, model: <pinned-in-release> }

fast_cheap_structured_output:
  - { provider: anthropic, model: claude-haiku-4-5-20251001 }
  - { provider: openai, model: <pinned-in-release> }

long_context_extraction:
  - { provider: anthropic, model: claude-opus-4-7 }
  - { provider: anthropic, model: claude-sonnet-4-6 }
```

Concrete model identifiers in the ranking file are pinned per release. The model identifiers above reflect Anthropic's current public model strings (per the product-self-knowledge skill); OpenAI entries are filled at release time after the maintainer confirms current production identifiers. Users override via `~/.cyberlab-gen/model_rankings.yaml`.

### 3.4 Resolution rules

- A capability hint must have at least one entry whose provider is configured. Otherwise startup fails with a clear error pointing to which capability lacks coverage.
- The first reachable entry in the ranked list wins. **Reachability is at resolution time only**: a provider with a configured API key is reachable for resolution. If the call that follows fails because the key is invalid or quota is exceeded, that's a `HardFailure` (§6.3) — the resolver does **not** silently fall back to the next entry. Falling back across vendors mid-call is exactly the "automatically switch providers mid-run" behavior `pipeline.md §3.7` forbids. The user fixes their config and resumes.
- Resolution is logged at `INFO` with the resolved `(provider, model)`; the run report records every resolution for the cost-per-quality eval metric.
- The same hint inside a single run always resolves to the same model unless config changes mid-run (not a v1 scenario).

### 3.5 Vendor-specific reasoning controls map from the capability hint

Modern frontier models expose vendor-specific reasoning controls — Anthropic's `output_config.effort` (with levels `low`, `medium`, `high`, `xhigh`, `max` on Opus 4.7 and Sonnet 4.6), OpenAI's reasoning effort knob, and so on. These are **not** surfaced on the `Provider` interface. Each adapter maps from the capability hint to its vendor's reasoning-control values:

- `HIGH_QUALITY_REASONING` → Anthropic adapter sets `effort=xhigh` (or `high` on models that don't support `xhigh`).
- `FAST_CHEAP_STRUCTURED_OUTPUT` → adapter sets `effort=low` (or the model's lowest supported tier).
- `LONG_CONTEXT_EXTRACTION` → adapter sets `effort=medium` (long-context work benefits from sustained reasoning but rarely needs maximum depth).

The mapping table lives in each adapter alongside the model-specific quirks (which models support which effort levels, etc.). Agents never reach for `effort` directly — the capability hint encodes the reasoning-depth intent at the architectural layer, and the adapter translates.

---

## 4. The `Provider` interface

### 4.1 Core types

```python
# cyberlab_gen/providers/base.py

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


T_Output = TypeVar("T_Output", bound=BaseModel)
# Note: T_Output must be a Pydantic BaseModel. Agents that need to produce
# a primitive (str, int) or list output wrap it in a Pydantic envelope class
# (e.g., class ItemList(BaseModel): items: list[str]). This keeps static-schema
# validation uniform and lets pyright track shapes end-to-end.


class CapabilityHint(StrEnum):
    """Canonical definition; the §3.1 duplicate is illustrative only."""
    HIGH_QUALITY_REASONING = "high_quality_reasoning"
    FAST_CHEAP_STRUCTURED_OUTPUT = "fast_cheap_structured_output"
    LONG_CONTEXT_EXTRACTION = "long_context_extraction"


class AgentLabel(StrEnum):
    """Stable identifiers for the closed set of system agents.

    Used for cost-ledger attribution and by-agent reporting. The closed-set
    discipline matches the architecture's "ten agents, named in agents.md"
    framing; free-form strings here would silently break by-agent rollups
    on a typo. The values match agents.md §0 enumeration.
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

    The provider layer translates this into the vendor SDK's tool format.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for tool input


class ToolCall(BaseModel):
    """A tool call the model made; the ToolExecutor runs it and the result
    flows back as a Message with role=TOOL and matching tool_call_id."""
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Result of executing a tool call. Embedded into the next Message by
    the tool-use loop driver."""
    model_config = ConfigDict(extra="forbid")

    call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """A single conversation turn.

    Content shape depends on role:
    - SYSTEM / USER: `content` is a non-empty string; `tool_calls` and
      `tool_call_id` are absent.
    - ASSISTANT: `content` carries any text the model emitted (possibly
      empty if the turn is pure tool-call); `tool_calls` is non-empty when
      the model invoked tools.
    - TOOL: `content` is the tool result's string content; `tool_call_id`
      references the originating assistant tool_call.

    The provider layer constructs assistant-with-tool_calls and tool-role
    messages internally when driving complete_with_tools; the full
    conversation is returned in ProviderResponse.conversation so the run
    report and eval harness have audit visibility.
    """
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # required when role == TOOL

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
    """Token counts for one provider call.

    Per pipeline.md §3.5: per-model cost tracking lives at the provider layer.
    Decimal (not float) for cost: accumulated rounding error across many
    calls would corrupt by-agent and by-model rollups in the run report.
    """
    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Total cost computed by the cost ledger using model-specific pricing.
    cost_usd: Decimal


class ProviderResponse(BaseModel, Generic[T_Output]):
    """The structured response from a provider call.

    Generic over the agent's declared output schema (T_Output: BaseModel).
    `conversation` contains the full message sequence the provider sent and
    received during a complete_with_tools loop (including intermediate
    assistant tool-call messages and tool-result messages). For plain
    complete() calls, conversation is the input messages plus the final
    assistant message — short and self-contained.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: T_Output
    raw_text: str  # the parsed-from text; kept for debugging and audit trail
    usage: TokenUsage
    model: str  # the concrete model that produced this response
    provider: str  # vendor (anthropic, openai, ...)
    conversation: list[Message] = Field(default_factory=list)
    # Tool calls observed across all iterations of the tool-use loop.
    # Empty for plain complete() calls.
    tool_calls: list[ToolCall] = Field(default_factory=list)


class Provider(ABC):
    """The single LLM-access interface for the entire system.

    All agent code calls into a Provider; no agent imports a vendor SDK directly.
    See pipeline.md §3.5 for the architectural rationale.

    Sampling parameters (temperature, top_p, top_k) are deliberately NOT
    surfaced. The architecture is built around capability hints and prompt
    engineering, not sampling tweaks. Current Anthropic models (Opus 4.7,
    Opus 4.6, Sonnet 4.6) return a 400 invalid_request_error if these
    parameters carry non-default values; the adapter must omit them entirely
    for those models, which is easier when the interface never accepts them.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        output_schema: type[T_Output],
        capability: CapabilityHint,
        agent_label: AgentLabel,
        max_tokens: int | None = None,
    ) -> ProviderResponse[T_Output]:
        """Run a single-turn structured-output completion. No tools.

        Agents that need tools use complete_with_tools instead — there is no
        manual-loop tool path. On structured-output parse failure, retries
        up to the configured retry budget per pipeline.md §3.7. Final failure
        raises ProviderError.MalformedOutput.
        """
        ...

    @abstractmethod
    async def complete_with_tools(
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
        """Run a tool-using loop that ends with a structured output.

        Internally drives the back-and-forth: model produces tool calls,
        ToolExecutor runs them, results are passed back as tool-role
        Messages, until the model produces a final structured output (or
        max_iterations is reached).

        On the final iteration, the model is expected to produce a structured
        output matching output_schema (no tool calls). If the model produces
        a tool call when max_iterations is reached, raises ProviderError.ToolLoopError
        (treated as agent failure per pipeline.md §3.7, routed to
        refinement-or-abandon per pipeline.md §3.2.12).

        max_iterations is a required parameter (no default) — callers must
        choose a per-agent value calibrated to the agent's expected loop
        depth (see architecture.md §8.4 calibration entry).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in ranking and reports."""
        ...
```

### 4.2 The `ToolExecutor` protocol

Tool execution is the caller's responsibility, not the provider's. The provider runs the tool-use *loop*; the caller's `ToolExecutor` runs each individual tool. This keeps vendor-specific tool-format translation in the provider while keeping tool semantics in the agent layer.

```python
class ToolExecutor(Protocol):
    """Executes a single tool call. Implemented by the agent layer.

    The Provider drives the tool-use loop; the ToolExecutor implements
    the tool semantics (registry lookup, external_lookup, propose_value_type, ...).
    """

    async def execute(self, call: ToolCall) -> ToolResult:
        ...
```

### 4.3 Why `complete` is generic over `output_schema`

Per `pipeline.md §3.5`: "Provider responses are parsed against the agent's declared output schema." Making `complete` generic over the output schema lets pyright type-check that the agent's downstream code uses the right shape. The provider layer:

1. Translates `output_schema` to the vendor's structured-output format (JSON Schema for OpenAI, the equivalent for Anthropic).
2. Parses the raw response against the schema using Pydantic.
3. Retries on parse failure per `pipeline.md §3.7`.
4. Returns a typed `ProviderResponse[T]` whose `output` field is statically typed as `T`.

This is the load-bearing reason for the generic type parameter. Without it, every agent would re-parse its provider response and pyright would lose track of the shape.

### 4.4 Why two methods (`complete` and `complete_with_tools`)

`complete` is the single-turn path: messages in, structured output out, no tools. Most agent calls take this path — the agent reads structured input, reasons, and produces structured output.

`complete_with_tools` is the tool-using-loop path: the model produces tool calls, the `ToolExecutor` runs them, results flow back, the loop repeats until the model produces a final structured output. Used by agents whose work requires fetching evidence (Extractor's external_lookup, Planner's registry queries, Critic's web_search, Repair Agent's read_lab_file).

The two methods are deliberately separate at the public API. There is no manual-tool path (e.g., `complete` returning tool calls for the caller to execute) — every tool-using agent uses the loop. This avoids a third pattern that would be tempting-but-rare and would force agents to write loop-driving code that the provider already knows how to do.

The two methods share the same retry, parsing, and cost-tracking machinery internally. The split is at the public API.

### 4.5 Streaming

v1 does not stream. The implementation plan §3.2 explicitly defers streaming as a Phase 0 over-architecting risk. The provider layer is built non-streaming-first; adding streaming later is a method-signature extension (`complete_streaming(...) -> AsyncIterator[...]`), not a redesign.

---

## 5. Cost tracking

### 5.1 The cost ledger

```python
# cyberlab_gen/providers/base.py (continued)

class CostLedger:
    """Accumulates token usage and cost across a single run.

    Created at run start, attached to the run context, read by the framework
    when deciding whether to emit a budget-overrun interrupt
    (pipeline.md §3.1.1).
    """

    def __init__(self, run_id: str, cap_usd: Decimal | None) -> None:
        self.run_id = run_id
        self.cap_usd = cap_usd
        self._entries: list[CostLedgerEntry] = []

    def record(self, entry: CostLedgerEntry) -> None: ...

    @property
    def total_usd(self) -> Decimal: ...

    def remaining_under_cap(self) -> Decimal | None:
        """None if no cap; else cap minus current total. May be negative."""
        ...

    def by_agent(self) -> dict[AgentLabel, Decimal]: ...

    def by_model(self) -> dict[str, Decimal]: ...

    def to_report_block(self) -> CostReportBlock: ...


class CallOutcome(StrEnum):
    """How a single provider call attempt ended.

    Per-attempt outcomes let the eval harness compute retry rates per agent
    and distinguish wasted spend (failed-after-retries) from healthy spend
    (success-first-try or retry_succeeded). Every billed attempt produces
    one CostLedgerEntry; a logical call that succeeds on retry 2 produces
    three entries (two FAILED, one SUCCESS) summing to the full billed cost.
    """
    SUCCESS = "success"
    FAILED = "failed"          # attempt that was retried; full cost still billed by vendor


class CostLedgerEntry(BaseModel):
    """One billed provider attempt.

    Entries are per-attempt, not per-logical-call: a logical call that
    succeeds after 2 retries produces 3 entries (2 FAILED + 1 SUCCESS).
    This makes retry rates per agent computable from the ledger alone.
    """
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    agent_label: AgentLabel
    provider: str
    model: str
    capability: CapabilityHint
    usage: TokenUsage
    outcome: CallOutcome
    purpose: str  # free-form short description, e.g., "main extraction call"


class CostReportBlock(BaseModel):
    """The serialized cost section of the run report (pipeline.md §3.6.3)."""
    model_config = ConfigDict(extra="forbid")

    total_usd: Decimal
    by_agent: dict[AgentLabel, Decimal]
    by_model: dict[str, Decimal]
    by_provider: dict[str, Decimal]
    entries: list[CostLedgerEntry]
```

### 5.2 Pricing tables

Per-model pricing lives in `cyberlab_gen/providers/pricing.yaml`. The provider layer reads it at startup and computes `cost_usd` on every response. Format:

```yaml
# Per-million-token prices in USD. Updated per release.
# Cache pricing follows vendor convention: cache_write is typically more
# expensive than input; cache_read is typically cheaper than input.
# Values below reflect current Anthropic pricing for Opus 4.7 (May 2026).
# 5-minute cache: write = 1.25x input. 1-hour cache: write = 2x input.

anthropic:
  claude-opus-4-7:
    input: 5.00
    output: 25.00
    cache_read: 0.50          # 10% of input rate
    cache_write_5min: 6.25    # 1.25x input rate
    cache_write_1h: 10.00     # 2x input rate
  # ...

openai:
  # filled at release time per current vendor pricing
```

Concrete prices ship with each release; users may override via `~/.cyberlab-gen/pricing.yaml`. The user file is **merged** with the bundled file (override on collision), not entirely replacing it like `model_rankings.yaml` does — this lets users adjust the price of a single model (e.g., to reflect a negotiated rate) without re-listing every model. Stale prices are a UX issue (cost estimates wrong) but not a correctness issue (the lab still generates).

### 5.3 Budget enforcement

The framework — not the provider — owns budget-overrun decisions. Per `pipeline.md §3.1.1`:

> When the framework estimates that the next stage or refinement iteration would push accumulated spend past a cap, it pauses and surfaces the choice to the user.

The provider layer simply records the usage and reports the total. The framework consults `CostLedger.remaining_under_cap()` before invoking the next stage and decides whether to interrupt.

### 5.4 The `--max-llm-cost` CLI flag

The CLI accepts `--max-llm-cost USD` (and reads `cost.max_usd` from config as a fallback). The value is plumbed into the run's `CostLedger` at startup. `None` means no cap.

---

## 6. Retries and failure semantics

The retry strategy implements `pipeline.md §3.7`. The provider layer owns it, not the agent layer.

### 6.1 Transient failures

Timeouts, transient 5xx errors, and 429 rate-limit responses are retried with exponential backoff. Since the pydantic-ai migration (ADR 0036) the owner differs by call type — there is no single framework-owned transient loop covering both:

- **LLM calls** — transient retry is handled by the **anthropic SDK** inside the pydantic-ai agent runtime, at the SDK's own default (currently 2 retries). The framework does not wrap LLM calls in its own transient loop; tuning this means the SDK/agent `max_retries`, not the `retries:` block below.
- **Ingestion fetch** (the HTTP blog fetch in `framework/ingestion.py`) — uses the framework's strategy from `providers/retries.py` (`TRANSIENT_RETRIES`): up to 3 attempts (initial + 2 retries), base delay 1s, exponential factor 2, jitter ±30%. All attempts count as one fetch from the caller's perspective; on exhaustion the fetch fails transiently and the framework writes a checkpoint per `pipeline.md §3.7`.

### 6.2 Malformed structured output

When the provider returns text that doesn't parse against `output_schema`, retries happen at **two distinct layers**. This is the two-layer model resolved in `dev/decisions/0018-agent-call-surface-structural-retry.md`; `pipeline.md §3.7` describes the same two layers from the stage's side, and the two sections should be read together.

**Layer A — provider-internal malformed-output retry (this layer).** The provider re-prompts the model with the previous parse error:

- Retry up to 2 times (initial + 1 retry). The default was lowered from 3 to 2 to cap the worst-case provider call count for a persistently malformed stream (ADR 0018).
- Each retry passes the previous parse error back to the model as a system-side note ("Your previous response did not match the schema; here is the error: ...").
- On exhaustion the provider raises `ProviderError.MalformedOutput`.

This provider-internal retry count is *not* charged to the agent's stage retry budget — parse-failure re-prompting is a provider-layer concern, distinct from agent-quality concerns. This matches the `architecture.md §1.7` retry-vs-refinement distinction.

**Layer B — agent-stage structural retry (the call surface).** A `ProviderError.MalformedOutput` from Layer A surfaces to the agent call surface (`cyberlab_gen.agents.call_surface`) as **one** stage-level structural failure. The call surface may itself re-invoke the whole stage up to its own structural-retry budget (this is the budget `pipeline.md §3.7` means by "counted against the retry budget"). Only when *that* budget is exhausted does it raise the agent-failure path (`errors.AgentFailure`), which the orchestrator routes to refinement-or-abandon per `pipeline.md §3.2.12`. This is still structural retry, never refinement.

### 6.3 Hard failures

Quota exceeded, invalid API key, "no provider configured": raise `ProviderError.HardFailure` immediately. No retry. The framework presents a clear actionable error to the user.

### 6.4 Error hierarchy

```python
# cyberlab_gen/providers/errors.py

class ProviderError(CyberlabGenError):
    """Base for all provider-layer errors."""

class TransientFailure(ProviderError):
    """Retries exhausted on a transient condition."""

class MalformedOutput(ProviderError):
    """Provider returned text that did not parse against the declared schema."""

class HardFailure(ProviderError):
    """Non-retryable provider error (quota, auth, no provider configured)."""

class CapabilityUnreachable(ProviderError):
    """The requested capability hint has no reachable model in the ranking."""

class ToolLoopError(ProviderError):
    """Tool-use loop exceeded max_iterations without producing final output."""
```

---

## 7. The `MockProvider`

Per `implementation-plan.md §3.2`: a mock provider that returns canned responses without making API calls. This is the load-bearing test fixture for the entire project.

```python
# cyberlab_gen/providers/mock_provider.py

class MockProvider(Provider):
    """Provider used in tests. No real API calls.

    Tests register canned responses by capability + agent_label + a matcher
    on the messages. Calls without a matching canned response raise
    UnmatchedMockCall, surfacing test gaps loudly rather than hanging
    or returning empty.
    """

    @property
    def name(self) -> str:
        return "mock"

    def register(
        self,
        *,
        capability: CapabilityHint,
        agent_label: AgentLabel,
        message_matcher: Callable[[list[Message]], bool] | None = None,
        response: BaseModel,
        usage: TokenUsage | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        """Register a canned response for matching calls.

        If `usage` is None, falls back to the default registered via
        register_default_usage. If neither is set, a minimal zero-cost
        TokenUsage is used.
        """
        ...

    def register_default_usage(self, usage: TokenUsage) -> None:
        """Default TokenUsage when register() doesn't specify one. Useful for
        tests where the cost doesn't matter and the same default should apply
        across many registrations."""
        ...
```

The mock provider is used by:

- All unit tests for agent code (agents are tested in isolation against canned responses).
- The integration smoke test that verifies the provider abstraction end-to-end (`implementation-plan.md §3.4`).
- The eval harness's regression mode (replay-from-cassettes, useful for measuring code-level changes that should not affect output).

### 7.1 Unmatched calls fail loudly

If the test calls into the provider with no matching registration, `MockProvider` raises `UnmatchedMockCall` with a message showing the capability, agent label, and the first ~200 chars of the messages. This is intentional: silent fallbacks in test infrastructure are the worst kind of bug.

---

## 8. Vendor adapter conventions

Each vendor adapter follows the same shape:

### 8.1 `AnthropicProvider`

- Wraps a **pydantic-ai `Agent`** over an `AnthropicModel` (which wraps the official
  `anthropic` SDK) — per ADR 0036. The agent runtime supplies the structured-output
  path, the tool-use loop, and the within-call malformed-output retry; the adapter
  resolves the capability hint to the ranked anthropic model, builds the agent, and
  drives it via `agent.iter` so token usage is captured on success *and* on a raise.
- Translates `output_schema` (Pydantic model) → the agent's `output_type`. The
  underlying convention is still tool-call-as-structured-output (pydantic-ai's default
  forced-tool output mode): a single output tool whose schema is the model's JSON
  Schema, whose call arguments are parsed as the structured output.
- Translates each `ToolDefinition` → a pydantic-ai tool (`Tool.from_schema`) whose
  implementation calls back into the `ToolExecutor`.
- Token usage (including the cache-read/cache-write split) comes from pydantic-ai's
  `RunUsage`; the USD figure is computed by `cost_ledger.compute_cost` (Decimal, the
  bundled `pricing.yaml`) and recorded in `TokenUsage`.
- A truncated emit (`finish_reason == 'length'`, or pydantic-ai's `IncompleteToolCall`)
  raises `EmitTruncated` with the billed usage attached (ADR 0033/0036).

### 8.2 `OpenAIProvider`

- Wraps the official `openai` SDK.
- Uses OpenAI's `response_format={"type": "json_schema", ...}` for structured output.
- Translates `ToolDefinition` → OpenAI's tool format.

### 8.3 Adapter testing

Each adapter has:

- Unit tests for the translation logic. For `AnthropicProvider` (pydantic-ai-backed, ADR 0036) this uses pydantic-ai's offline `FunctionModel`/`TestModel` to drive the agent without network or API key; a raw-SDK adapter would instead mock SDK responses (the SDK's own mock or `respx`).
- Integration tests gated by an env var (`CYBERLAB_GEN_INTEGRATION_TESTS=1`) that hit the real vendor with a free-tier-friendly minimal call. Skipped in normal CI; run before each release.

### 8.4 Adding a new vendor

The recipe:

1. Implement `Provider` in `cyberlab_gen/providers/<vendor>_provider.py`.
2. Add entries to `model_rankings.yaml` referencing this vendor.
3. Add pricing entries to `pricing.yaml`.
4. Add credential lookup to the config schema (env var, config-file key).
5. Write unit tests for the translation logic and an opt-in integration test.

No agent code changes. This is the architectural payoff for `pipeline.md §3.5`.

---

## 9. Multi-model jury support

Per `pipeline.md §3.5`: "The jury layer can specify a different provider/model than the agent it judges."

The provider layer exposes:

```python
# cyberlab_gen/providers/ranking.py

class ProviderRegistry:
    """Discovers configured providers, resolves capability hints, selects diverse models."""

    def resolve(self, capability: CapabilityHint) -> tuple[str, str]:
        """Returns (provider_name, model_id) for the best reachable entry."""
        ...

    def resolve_diverse_from(
        self,
        capability: CapabilityHint,
        avoid: tuple[str, str],
    ) -> tuple[tuple[str, str], DiversityLevel]:
        """Returns the best-reachable (provider, model) that differs from `avoid`.

        DiversityLevel:
          - DIFFERENT_PROVIDER: best case; jury uses a different vendor.
          - DIFFERENT_MODEL_SAME_PROVIDER: only one vendor configured; jury uses
            a different model from same vendor. Logged as degraded per
            pipeline.md §3.5.
          - SAME_MODEL_FORCED: only one model reachable; jury and judged agent
            share a model. Recorded with explicit weak-signal flag in the report.
        """
        ...


class DiversityLevel(StrEnum):
    DIFFERENT_PROVIDER = "different_provider"
    DIFFERENT_MODEL_SAME_PROVIDER = "different_model_same_provider"
    SAME_MODEL_FORCED = "same_model_forced"
```

The diversity level is recorded in the run's jury section per `pipeline.md §3.5` ("multi_model_diversity: degraded_same_provider"). The eval harness consumes this to measure whether multi-model juries actually reduce false-approval and false-rejection rates.

---

## 10. Configuration surface

The provider layer reads from `~/.cyberlab-gen/config.yaml`:

```yaml
providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY  # default; override if you use a different var
    # api_key: "..."                # discouraged; env var preferred
    # base_url: "..."               # for Anthropic-compatible proxies
  openai:
    api_key_env: OPENAI_API_KEY

cost:
  max_usd: 10.00                    # default cap; --max-llm-cost overrides per run
  show_estimate_before_each_stage: true

retries:
  transient_max_attempts: 3         # ingestion-fetch transient strategy (retries.py); LLM transient is the anthropic SDK default — see §6.1
  malformed_output_max_attempts: 2  # provider-internal; default 2 (initial + 1 retry) per §6.2 / ADR 0018
  base_delay_seconds: 1
  exponential_factor: 2
```

Missing keys fall back to documented defaults. Missing API keys for a provider mean that provider is "not configured"; its entries in the ranking are unreachable.

---

## 11. What the provider interface deliberately does not do

These are listed so the implementing agent does not add them on the assumption they belong here:

- **Prompt construction.** Agents construct their own prompts. The provider takes a `list[Message]` and treats them as opaque.
- **Tool execution.** Agents execute tools (via `ToolExecutor`). The provider drives the tool-use loop in `complete_with_tools` but never executes a tool itself — it calls the supplied executor. There is no manual-tool path (e.g., `complete` returning tool calls for the caller to execute); see §4.4.
- **Refinement decisions.** Whether to re-run an agent after a low-quality output is a framework decision (refinement coordinator), not a provider decision.
- **Cross-provider fallback mid-call.** Per `pipeline.md §3.7`: "Automatically switch providers mid-run [...] the pipeline never makes that decision silently." The provider layer never falls over to a different vendor on failure.
- **Caching the agent's *output*.** Token-level prompt caching is honored per vendor; semantic caching of "this prompt → this output" is not provided.
- **Conversation memory across calls.** Each `complete()` and `complete_with_tools()` call is stateless from the provider's view — the provider does not remember prior calls. Conversation state across calls is the caller's responsibility (relevant for the Repair Agent's multi-turn fix-session loop, where the framework maintains conversation history between Repair Agent calls per `agents.md §5.16`). The conversation *within* a single `complete_with_tools` call is the provider's responsibility and is returned in `ProviderResponse.conversation` for audit.

---

## 12. Phase 0 implementation order

For the implementing agent doing Phase 0, the build order is:

1. `errors.py` — the error hierarchy. Tiny, gates everything else.
2. `base.py` — types (`Message`, `TokenUsage`, `ProviderResponse`, `CostLedger`), the `Provider` ABC, the `ToolExecutor` protocol. No vendor code yet.
3. `mock_provider.py` — implements `Provider` with canned responses. Lets you write tests for everything else.
4. `pricing.yaml`, `ranking.py` — capability resolution.
5. `retries.py` — retry strategy (testable against `MockProvider` configured to fail N times).
6. `anthropic_provider.py` — first real vendor.
7. `openai_provider.py` — second real vendor. By the time this lands, the abstractions have been exercised once and the second implementation surfaces any leakage.

The mock-provider-first order is deliberate: it lets every other piece be tested before a vendor SDK is involved.

---

## 13. Follow-up changes outside this document

Items consistent with the shapes in this document that need to land elsewhere. Mirrors the pattern from `schema-details.md §10`.

### 13.1 Add `max_iterations` calibration entry in `architecture.md §8.4`

The `complete_with_tools` method requires the caller to specify `max_iterations`. There is no architectural default; per-agent calibration is empirical. Add to `architecture.md §8.4` (post-launch calibration list):

> **Tool-use loop max_iterations per agent** (no v1 default; placeholders to be calibrated from eval data). Each tool-using agent declares its expected loop depth. Initial placeholders: Extractor 5 (external_lookup per CVE × N CVEs); Planner 5 (registry queries); Critic 6 (web_search budget per `§8.4` Critic web_search call budget × extra reasoning turns); Repair Agent 10 (longer interactive loops). Exceeding `max_iterations` produces `ToolLoopError`, routed to refinement-or-abandon per `pipeline.md §3.2.12`.

### 13.2 Adapter-level constraints from current Anthropic models

These are model-specific quirks the AnthropicProvider adapter must handle. Not interface-level (the interface deliberately doesn't expose vendor knobs), but documented here so the adapter implementer is aware:

- **No `temperature`, `top_p`, `top_k` on Opus 4.7.** Setting non-default values returns a 400 `invalid_request_error`. The interface doesn't accept these parameters at all (§4.1 design note); the adapter omits them from request payloads unconditionally.
- **No prefill on Opus 4.7, Opus 4.6, Sonnet 4.6, Mythos Preview.** Sending a request whose final message is `role: assistant` returns a 400 `invalid_request_error` ("This model does not support assistant message prefill. The conversation must end with a user message."). The provider's tool-use loop must end every model-facing message list with a user or tool message, never an assistant message. The adapter strips trailing assistant messages defensively, or refuses to send them with a clear `ProviderError`.
- **`thinking.type: "enabled"` deprecated on Opus 4.7.** Replaced by `thinking.type: "adaptive"` plus `output_config.effort` for depth control. Captured in the §3.5 capability-to-effort mapping; the adapter translates capability hint → `effort` value.
- **`output_config.effort` not supported by every model.** Older models or smaller models may support a subset (e.g., only `medium`). The adapter's mapping table records per-model `effort` support; falling back to the highest-supported effort at-or-below the requested level matches Anthropic's documented behavior.

### 13.3 `AgentLabel` enum belongs in `agents.md`

`AgentLabel` is defined in §4.1 with values matching the ten-agent enumeration in `agents.md §0`. The canonical list should live in `agents.md`; this document re-exports the enum from `cyberlab_gen.providers` as a convenience import. A short `agents.md` update would note: "The closed set of agents in v1 is also exposed as `AgentLabel: StrEnum` in `cyberlab_gen.providers` for cost-ledger attribution and by-agent reporting."

### 13.4 Phase 0 test: pricing file loads against pricing.yaml schema

The implementation plan's Phase 0 already includes the registry-load test (`implementation-plan.md §3.4` check 4). Add a parallel check for `pricing.yaml`: load the bundled file, assert every model in `model_rankings.yaml` has a corresponding pricing entry (or fail with a clear "missing pricing for model X" error). Catches the failure mode where a new model lands in the ranking without pricing, silently breaking cost calculations.

---

*End of provider interface document. See `pipeline.md §3.5` and §3.7 for architectural rationale; this document specifies the implementation contract.*
