# 0041 — Local Phoenix observability via pydantic-ai native OpenTelemetry

**Date:** 2026-06-06
**Phase:** 1 (operational-foundation pass, outcomes #1/#2)
**Architecture refs:** `architecture.md §1.5` (pydantic-ai is the typed agent layer).
Builds directly on ADR 0036 (the agent layer now runs on pydantic-ai, so its **native**
OpenTelemetry is the correct instrumentation path) and ADR 0023 (the orchestrator surface,
lightly touched here for stage spans).

## Context

A provider-backed run spends real money on multi-step LLM calls, but there was no way to
*see* a run: which model, how many tokens, the cost, the stop reason, the prompt/response,
the tool calls, or the shape of the pipeline (extract → validate → jury → enrich). The
agent layer now runs on pydantic-ai (ADR 0036), which emits OpenTelemetry spans natively;
Arize Phoenix is a local, self-hosted trace viewer that ingests OTLP. The remaining work
was to point pydantic-ai's spans (plus manual spans for the LangGraph stages) at a local
Phoenix — and to make sure tracing never affects a normal run when Phoenix isn't there.

## Decision

### Native pydantic-ai OTel, exported to a local Phoenix

`cyberlab_gen/tracing_setup.setup_tracing()` configures a global `TracerProvider` that
exports OTLP to a local Phoenix (default `http://localhost:6006`) and calls
`Agent.instrument_all(True)` so every pydantic-ai LLM call becomes a span. The provider +
exporter are set up via `phoenix.otel.register(...)` (`arize-phoenix-otel`). Local only —
no Logfire/cloud export; data stays on the machine. Called once at each entry point
(`cli/main._main`, `eval/runner/cli.main`) after `setup_logging`.

### Never crash, never block, off-by-default-unless-present

- **Probe first.** `setup_tracing` does a fast TCP check of the endpoint *before importing
  any OTel/Phoenix module*; when nothing is listening (the common case) it no-ops with zero
  import cost. `CYBERLAB_GEN_TRACING` overrides: `auto` (default), `off`, `on`.
- **Never raises.** A missing extra (`ImportError`) or any setup error is logged and
  swallowed; the run proceeds untraced.
- **Non-blocking export.** Spans batch in the background and drop silently if the collector
  disappears mid-run.

### Stage spans for the LangGraph pipeline (ADR-0023 surface touch)

Each node in `build_pipeline` is wrapped (`_traced_async` / `_traced_sync`) in a
`stage_span("extract"|"validate_layer1"|"jury"|"enrich")`, so the stage tree nests under
the agent spans in Phoenix. `stage_span` is a **no-op context manager when tracing is off**,
so this is behaviour-neutral and zero-cost by default. This is an additive, traced-only
code touch of the ADR-0023-locked builder (no signature or routing change), recorded here
as an amendment alongside ADR 0040's checkpointer parameter.

### Dependencies — native only, deliberately NOT the OpenInference instrumentor

The observability stack is an **optional extra** (`[observability]` → `arize-phoenix-otel`,
which transitively pulls the OTel SDK + OTLP exporter), kept out of the default install so a
normal install stays lean. `uv sync --extra observability` enables it.

**Deviation from the handoff dep list (flagged).** The handoff suggested adding
`openinference-instrumentation-pydantic-ai`. We deliberately do **not** add it: pydantic-ai
emits spans natively via `Agent.instrument_all`, and layering the OpenInference
pydantic-ai instrumentor on top would instrument the same layer twice — the exact
double-count of spans/tokens/cost that ADR 0036 warns against (it made the same call about
the anthropic-SDK instrumentor). Native instrumentation is the single source of spans. If
Phoenix's UI later proves to need OpenInference semantic conventions, that is a follow-up
that *replaces* native instrumentation, not one that runs alongside it.

## Alternatives considered

- **OpenInference pydantic-ai instrumentor instead of native.** Rejected for now: ADR 0036
  settled on native pydantic-ai OTel; running both double-counts. Single-source spans.
- **Always-on tracing.** Rejected: it would add an exporter/probe cost to every run and fail
  noisily when Phoenix is down. Auto-detect + no-op is invisible until a Phoenix is up.
- **Ship the OTel deps in the default install.** Rejected: they are heavy and only needed
  when a developer wants traces; an optional extra keeps the default footprint small.
- **Import OTel/Phoenix at module load.** Rejected: probing first keeps the disabled path
  free of the import and lets `setup_tracing`'s no-op paths be tested without the extra.

## Consequences

- New `cyberlab_gen/tracing_setup.py` (`setup_tracing`, `stage_span`,
  `reset_tracing_for_tests`); new `[observability]` extra (`arize-phoenix-otel`).
- `build_pipeline` wraps each node in a stage span (no-op when off); both entry points call
  `setup_tracing()` after logging; the test conftest forces `CYBERLAB_GEN_TRACING=off` and
  resets tracing per test so a developer's local Phoenix can't perturb the suite.
- New tests: off → disabled; auto + unreachable → disabled and non-raising; idempotent;
  `stage_span` is a clean no-op when disabled. The reachable/configured path is exercised
  manually with a running Phoenix (`docker run -p 6006:6006 -p 4317:4317
  arizephoenix/phoenix:latest`, view at `http://localhost:6006`).
- The optional `phoenix.otel` import is guarded for type-checking so the strict pyright gate
  passes whether or not the extra is installed.
