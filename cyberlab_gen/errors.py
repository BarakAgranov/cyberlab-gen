"""Project exception hierarchy.

Architectural source: ``coding-conventions.md`` §6.1. Root is
``CyberlabGenError``; subdivisions follow the architecture's stage
boundaries. Stage subclasses land in the tasks that first raise them
(per ADR 0009). Phase 0 populates the registry branch (Task 4) and the
provider branch (Task 5a); the remaining stage classes
(``IngestionError``, ``ExtractionError``, ``PlanningError``,
``GenerationError``, ``ValidationLayerError``) land in Phase 1+.

Every error carries the structured context §6.1 requires (``stage``,
``run_id``, ``cause``). Phase 0 has no pipeline runner yet so ``run_id``
is always ``None``; ADR 0009 records the deferral and Phase 1's runner
task wires it through.

Use ``raise X from Y`` per §6.1 so ``__cause__`` chains preserve the
original error for the structured run report.

Provider errors live here, not under ``cyberlab_gen/providers/errors.py``,
per ADR 0009's single-hierarchy rule. ``provider-interface.md`` §2 and
§6.4 still show ``cyberlab_gen/providers/errors.py``; that is a known
doc-improvement note (recorded in the Phase 0 execution log) and the
authoritative location is here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.providers.base import TokenUsage


class CyberlabGenError(Exception):
    """Root of every framework-raised exception.

    Carries structured context (``stage``, ``run_id``, ``cause``) for
    inclusion in the run report. ``cause`` mirrors ``__cause__`` for
    callers that read attributes; ``raise X from Y`` still populates the
    traceback's ``__cause__`` separately.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.run_id = run_id
        self.cause = cause


class RegistryError(CyberlabGenError):
    """Registry-stage errors (load, merge, lookup).

    Pins ``stage='registry'`` so the run report can group registry
    failures without each call site re-supplying it.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="registry", run_id=run_id, cause=cause)


class RegistryLoadError(RegistryError):
    """Raised when a registry YAML file fails to load or validate.

    Carries the offending file path so user-facing output can point at
    the file directly. The underlying error (YAML parse failure or
    ``pydantic.ValidationError``) is preserved as ``cause`` and as
    ``__cause__`` via ``raise ... from``.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, run_id=run_id, cause=cause)
        self.path = path


class ProviderError(CyberlabGenError):
    """Provider-stage errors (LLM call surface).

    Pins ``stage='provider'`` so the run report can group provider
    failures without each call site re-supplying it. The five subtypes
    below partition the failure modes ``provider-interface.md`` §6
    enumerates:

    - ``TransientFailure`` — retries exhausted on a transient condition
      (timeout, 5xx, 429). §6.1.
    - ``MalformedOutput`` — model produced text that did not parse against
      the declared ``output_schema`` after the retry budget. §6.2.
    - ``HardFailure`` — non-retryable (quota, auth, no provider). §6.3.
    - ``CapabilityUnreachable`` — the requested capability hint has no
      reachable model in the configured ranking. Raised by the resolver
      (Task 5b); the class lands here in 5a alongside the others. §6.4.
    - ``ToolLoopError`` — tool-use loop exceeded ``max_iterations`` without
      producing a final structured output. §6.4.

    Note: the brief audit found ``CapabilityUnreachable`` omitted from the
    Task 5a brief's enumeration; the canonical six-class set in §6.4 is
    what ships.

    ``usage``/``model`` carry the vendor-billed token usage accumulated *before*
    the call raised, plus the resolved model id. A failed structured-output call
    (a truncated or malformed emit that exhausted its retries, a tool loop that
    never converged) was still billed for every attempt; the provider attaches
    that billed usage to the raised error so the cost-recording layer can record
    the spend even when no ``ProviderResponse`` is returned — otherwise the real
    cost exceeds the reported cost and the cost cap goes blind (ADR 0033). Both
    default ``None`` (no billing happened, or the layer below does not attach).
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        cause: BaseException | None = None,
        usage: TokenUsage | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message, stage="provider", run_id=run_id, cause=cause)
        self.usage = usage
        self.model = model


class TransientFailure(ProviderError):  # noqa: N818 -- name locked by provider-interface.md §6.4
    """Retries exhausted on a transient condition (timeouts, 5xx, 429).

    ``provider-interface.md`` §6.1: the provider attempts up to 3 calls
    with exponential backoff and ±30% jitter. If all attempts fail with
    transient errors, this is raised and the framework checkpoints per
    ``pipeline.md`` §3.7.
    """


class MalformedOutput(ProviderError):  # noqa: N818 -- name locked by provider-interface.md §6.4
    """Provider returned text that did not parse against the declared schema.

    ``provider-interface.md`` §6.2: 3 attempts including a system-side
    note carrying the previous parse error. Final failure raises this.
    Counted distinctly from agent-quality refinement retries.
    """


class EmitTruncated(MalformedOutput):
    """A structured-output emit was cut off at the output-token limit (ADR 0033).

    A special case of :class:`MalformedOutput`: the emit failed schema validation
    *because* the vendor stopped at ``max_tokens`` (``stop_reason == "max_tokens"``,
    the authoritative truncation signal) mid-emit, not because the model produced
    deliberately invalid content. The two are distinguished because their remedies
    are opposite: a malformed-but-complete emit is worth one re-prompt, but a
    truncated emit will regenerate the same oversized output and truncate again at
    the same budget — retrying only burns money.

    It therefore subclasses ``MalformedOutput`` (it *is* a malformed parse) but is
    **never retried**: the provider raises it immediately instead of spending its
    malformed-output budget, and the agent call surface re-raises it instead of
    counting it against the structural-retry budget (ADR 0018/0032 contract,
    amended by ADR 0033). The message names the remedy (raise ``max_tokens`` or
    shorten the input) so a halted run's ``halt_reason`` is actionable.
    """


class HardFailure(ProviderError):  # noqa: N818 -- name locked by provider-interface.md §6.4
    """Non-retryable provider error (quota, auth, no provider configured).

    ``provider-interface.md`` §6.3: no retry; the framework surfaces a
    clear actionable error to the user.
    """


class CapabilityUnreachable(ProviderError):  # noqa: N818 -- name locked by provider-interface.md §6.4
    """The requested capability hint has no reachable model in the ranking.

    ``provider-interface.md`` §3.4 + §6.4: capability resolution time
    only — once a (provider, model) is chosen, mid-call vendor fallback
    is forbidden. The Task-5b ``ProviderRegistry`` raises this when no
    entry whose provider is configured exists for the hint.
    """


class ToolLoopError(ProviderError):
    """Tool-use loop exceeded ``max_iterations`` without final structured output.

    ``provider-interface.md`` §6.4 + §4.1: when the model produces a tool
    call on the final iteration of ``complete_with_tools``, the provider
    raises this. Treated as agent failure per ``pipeline.md`` §3.7.
    """


class AgentFailure(CyberlabGenError):  # noqa: N818 -- "Failure" is the architectural term, not "Error"
    """An agent stage exhausted its structural-retry budget.

    Raised by the agent call surface (``cyberlab_gen.agents.call_surface``)
    after the provider repeatedly returns ``MalformedOutput`` — i.e. the
    provider's own malformed-output retries (``provider-interface.md`` §6.2)
    were exhausted and the call surface's stage-level structural-retry budget
    (``pipeline.md`` §3.7) was then exhausted too. This is the "agent-failure
    path"; the orchestrator (Phase 1 Task 6) routes it to
    refinement-or-abandon per ``pipeline.md`` §3.2.12.

    Distinct from ``ProviderError``: the provider succeeded mechanically; the
    model could not produce a schema-valid result for this stage. Distinct from
    refinement: this is *structural* retry, never quality-driven
    (``architecture.md`` §1.7). The two-layer budget is recorded in ADR 0018.
    Subclasses ``CyberlabGenError`` directly (not ``ProviderError``) — it is an
    agent-stage outcome, not a provider-layer error.
    """


class ConfigError(CyberlabGenError):
    """A required configuration or bundled resource is missing or invalid.

    Raised by the prompt loader (``cyberlab_gen.agents.prompts``) when an
    agent's base prompt file is absent. A base prompt is a bundled, packaged
    resource; its absence is a packaging/config fault surfaced to the user, not
    a provider or agent-quality failure.
    """


class IngestionError(CyberlabGenError):
    """Ingestion-stage errors (fetch, normalize, cache).

    Pins ``stage='ingestion'`` so the run report can group ingestion failures
    without each call site re-supplying it. Raised by
    ``cyberlab_gen.framework.ingestion`` (``pipeline.md`` §3.2.1,
    ``implementation-plan.md`` §4.2). The three subtypes below partition the
    failure modes §4.2 enumerates; all three fail with a clear message and
    *never* attempt to bypass the obstacle (``implementation-plan.md`` §4.6
    risks). Carries the offending ``url`` so user-facing output can name it.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="ingestion", run_id=run_id, cause=cause)
        self.url = url


class UnreachableUrlError(IngestionError):
    """The URL could not be fetched at all (DNS, connect, timeout, 4xx/5xx).

    ``implementation-plan.md`` §4.2: "URL unreachable → fail with clear
    message." Raised after transient-retry exhaustion (``pipeline.md`` §3.7).
    """


class PaywallError(IngestionError):
    """The response indicates a paywall, not readable content.

    ``implementation-plan.md`` §4.2: paywall detection (HTTP 403, very-short
    body) fails with a clear message. The framework does *not* attempt to
    bypass the paywall (CLAUDE.md hard rule; §4.6 risks).
    """


class BotDetectedError(IngestionError):
    """The response is a bot-detection / anti-automation interstitial.

    ``implementation-plan.md`` §4.2: bot-detected (Cloudflare interstitial,
    etc.) fails with a clear message. The framework does *not* attempt to
    solve a CAPTCHA or evade bot detection (CLAUDE.md hard rule; §4.6 risks).
    """


class ExtractionError(CyberlabGenError):
    """Extraction-stage errors (``pipeline.md §3.2.2``, ``agents.md §5.4``).

    Pins ``stage='extraction'``. Raised by the Extractor stage
    (``cyberlab_gen.agents.extractor``) when its *content-level* retry budget is
    exhausted: the provider returned a structurally valid ``AttackSpec`` but it
    repeatedly failed a framework check — search-before-claim (a
    ``source: external_api`` field with no matching tool call,
    ``schema.md §4.15``) or MITRE/CVE hallucination (an id absent from the
    bundled MITRE catalog / NVD, ``pipeline.md §3.2.2``).

    Distinct from ``AgentFailure`` (the call surface's *structural*-malformation
    budget, ADR 0018) and from ``ProviderError`` (the provider succeeded
    mechanically). This is a *retry* outcome, never refinement
    (``architecture.md §1.7``). The two budgets are independent per ADR 0021.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="extraction", run_id=run_id, cause=cause)


class ValidationError(CyberlabGenError):
    """A mechanical Validator layer halted the pipeline (``validation.md §6.4``).

    Pins ``stage='validation'``. Raised by the orchestrator
    (``cyberlab_gen.framework.orchestrator``) when Validator Layer 1 reports
    structural violations that the responsible agent could not resolve within its
    *structural-retry* budget (``validation.md §6.10``, ``architecture.md §1.7``).

    Layer 1 failures route to the agent's **retry** mechanism, never to the
    refinement coordinator (``validation.md §6.10`` — the discipline this error
    enforces): when retry is exhausted the pipeline halts with this error rather
    than escalating to refinement. The unresolved findings are carried as
    ``findings`` so the run report can name every violation.

    Named ``ValidationError`` per the Task-6 brief and ADR 0009. It deliberately
    shadows the name of ``pydantic.ValidationError``; the two are never imported
    into the same namespace (this hierarchy is framework-stage errors, Pydantic's
    is the artifact-parse error), and the brief locks the name.
    """

    def __init__(
        self,
        message: str,
        *,
        findings: list[str] | None = None,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="validation", run_id=run_id, cause=cause)
        self.findings = findings or []


class EnrichmentError(CyberlabGenError):
    """Pre-Planner enrichment failed in a non-recoverable way (``pipeline.md §3.2.4``).

    Most enrichment failures (rate-limit, budget exhaustion, source not
    integrated) degrade *gracefully* — they record an ``unknown_from_blog``
    reason and continue rather than raising. This is reserved for the rare
    unrecoverable case (e.g., a malformed bundled registry).
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="enrichment", run_id=run_id, cause=cause)


class ExternalApiRateLimitError(EnrichmentError):
    """An external data source rate-limited a framework enrichment call.

    Distinct from the LLM-provider ``TransientFailure``: this is an external
    *data* source (NVD, GitHub, ...). The enrichment pass catches it and records
    the skipped lookup (``unknown_from_blog.reason: "external API rate-limited at
    enrichment time"``) rather than failing the run (``pipeline.md §3.2.4``).
    """
