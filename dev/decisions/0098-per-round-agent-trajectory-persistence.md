# 0098 — Persist the full per-round agent trajectory to the run dir

**Date:** 2026-06-17
**Phase:** 2 (Item 1 — pre-Task-7 enhancement; lands before Task 7 so Task 7's richer agent activity is captured from the start)
**Deciders:** maintainer (architect — ruled the four forks), implementing agent
**Architecture refs:** `architecture.md §1.5` (LLM/framework split — capture is pure observation),
`§1.6` (mechanical safety unaffected), `§2.3`/`pipeline.md §3.7` (the run dir). Builds on ADR 0039
(the run store saves every run on every exit path), ADR 0053 (the run store is the single
persistence authority), ADR 0068 (one shared persistence service), ADR 0041 (Phoenix tracing —
opt-in, ephemeral, never perturb a run), ADR 0036 (pydantic-ai agent runtime), ADR 0004 (base-class
discipline), `coding-conventions.md §5.5` (descriptive naming, no ordinal token).

## Context

The run dir persists only the **final** spec/manifest, the **final** jury verdict, enrichment, and
cost **counts** (`cost.yaml` is per-call metadata — tokens/model/outcome — never content). Every
intermediate round (each Planner/Extractor output, each Jury verdict + structured feedback +
rationale, each refine patch) is overwritten in `PipelineState` and lost; the only multi-round
survivor anywhere is `verdict_history`, a list of bare verdict enums. The actual per-round content
already exists in memory — every call returns a `ProviderResponse` carrying the structured output and
the full input conversation — but it is discarded after each call. It survives only in Phoenix traces,
which are opt-in, ephemeral, and a web UI, not portable files (ADR 0041). So "read the run dir alone
and reconstruct what every agent did and why, round by round" was not achievable.

The maintainer ruled four forks before implementation; this ADR records the design + how they were
built, and the one contract posture that needed an explicit ruling (§5).

## Decision

### 1. Split capture, correlated by round context — no second instrumentation path (the architecture)

The trajectory tuple `{agent, round, outcome, input, output}` is split across two layers that
already exist, fed into one per-run **`RunTrajectoryRecorder`** (`cyberlab_gen/state/trajectory.py`):

- The **provider chokepoint** (`CostRecordingProvider._record` / `_record_billed_failure`) — the
  single point every billed call in *both* pipelines already passes — supplies the **content**:
  agent identity, structured output, the input conversation, usage, success/failed. It notifies the
  recorder via a new `TrajectorySink` protocol (defined in `providers/base.py` beside `ToolExecutor`,
  so the provider depends only on the protocol, never on `state/` — no import cycle), invoked exactly
  like the existing `on_call` cost-echo seam. Capture lands **before** `_enforce_ceiling`, so a call
  whose spend crosses the catastrophe ceiling still records the round that blew the cap.
- The **orchestrator nodes** — the only layer that knows the **round index** and the **routing
  outcome** — call `recorder.enter_stage(round, stage)` before each agent call (so the provider-side
  records are grouped by round) and `recorder.routing_event(outcome)` after they resolve the route.

Reuses the *existing* per-call I/O; no second instrumentation path (the Phoenix concern). The recorder
is wired to the provider + orchestrator at run start by the runner's `enable_trajectory(handle)`,
mirroring `enable_checkpointing` (the provider is built before the run handle exists).

### 2. Always-on, best-effort (Fork: always-on vs flag)

Trajectory capture is **run-dir content**, written through the run store on every exit path — not the
opt-in Phoenix path. No new flag: the `extract`/`plan` dev/eval verbs exist precisely to be inspected.
ADR 0041's "never perturb a normal run" is honored by an **explicit best-effort guard, not by
coincidence**: the recorder's emitting methods (`record_call` / `record_failed_call` /
`routing_event`) catch-and-log broadly (`except Exception`, matching `checkpointing.py`'s
best-effort-observer pattern) — covering record construction and serialization, not just the
`OSError` that `RunHandle`'s writes already swallow — and the provider guards its sink invocation the
same way. So a capture failure (a serialization edge, a future record field) can **never** propagate
out of the chokepoint: it cannot crash a paid run, cannot mask a propagating `ProviderError`, and
cannot skip the mechanical catastrophe ceiling `_enforce_ceiling` runs immediately after
(`architecture.md §1.6`). This guard was added in response to the adversarial review of the diff,
which proved the original unguarded sink could bypass the ceiling and mask the error.

### 3. Structured "why" only; a typed hook for raw reasoning (Fork: what "why" means)

The captured **structured output** is each agent's "why": the Jury's `rationale` + field-level
feedback (`field_path`/`problem`/`suggested_fix`), the Planner/Extractor `llm_inference` provenance,
the `PlannerRefusal` detail. Raw model thinking is **not** captured: pydantic-ai exposes `ThinkingPart`
but extended thinking is not enabled and the Anthropic adapter drops it (would be a request-shape +
cost change needing its own ADR). `AgentCallRecord` carries an optional `reasoning` field that stays
`None` today — the typed hook for that future work.

### 4. Append-only `trajectory.jsonl` + content-addressed `blobs/` (Forks: on-disk shape; capture depth)

`trajectory.jsonl` is an **append-only, ordered event stream** of two record kinds (ArtifactModels,
one JSON object per line; ADR 0004):

- `AgentCallRecord` — one billed call: agent, round, stage, outcome, billed model + usage, the
  structured `output`, and `inputs` (the conversation it received).
- `RoutingEventRecord` — the framework's resolved route for a round (the outcome dimension).

Append-only is the most crash-robust streaming form (a halt/crash/Ctrl-C keeps every line already
written). The routing outcome is known only **after** the call's line is written, and append-only
means it can't be back-annotated — so it is its **own ordered event**, correlated to its call by
`round_index` + `stage`. Reading the stream in order reconstructs "producer made X → jury said revise
because Y → producer changed Z → jury approved".

**Depth = spine + input deduped by hash.** The "input it received" is captured, but a large constant
input (the schema-heavy system prompt, the blog body, the manifest) is written once to a
content-addressed `blobs/<sha256>.txt` and referenced by hash from each round's `MessageRef`, so it is
not duplicated across rounds; small structured inputs (rendered feedback) stay inline. Volume is a
non-issue for local single-user (bounded rounds; the one large item is deduped).

## Forks (pre-ruled by the maintainer)

1. **Capture depth** → spine + **input deduped by hash** (content-addressed `blobs/`).
2. **Reasoning** → **structured-only** (typed `reasoning` hook left for future extended-thinking).
3. **Activation** → **always-on, best-effort** (run-store content, not the Phoenix path).
4. **On-disk shape** → **append-only `trajectory.jsonl`**.

## The one contract posture (ruled here, not silently)

**Writing the trajectory through `RunHandle` extends the single persistence authority (ADR 0053/0068);
it is not a forbidden second persistence path.** ADR 0053's rule is that the run store is the *one*
mechanism that writes run artifacts and that what a run *produced* is read from the checkpoint, not a
parallel in-memory "remember the run" path. The trajectory is a different kind of data — an audit log
of the *process*, not the deliverable — and it is written through the same one writer (`RunHandle`'s
new `append_jsonl` / `write_blob`), incrementally, inheriting ADR 0039's best-effort / every-exit-path
discipline. No second writer of the run dir is introduced. ADR 0039's run-directory-contents block is
amended to list `trajectory.jsonl` + `blobs/` and to state the "one writer" invariant explicitly.

This is pure observation (`architecture.md §1.5`): nothing captured feeds back into routing, retry
budgets, or stop decisions. Mechanical safety (`§1.6`) is untouched.

## Consequences

- **Eval parity (a consequence, not a separate choice).** Capture lives in the shared
  provider+orchestrator path, so eval runs (the third run-store caller, ADR 0068) get trajectory files
  for free — consistent with "both pipelines".
- **FAILED rounds are metadata-only.** A billed-but-raised call exposes only usage+model (no
  `ProviderResponse`), so its `AgentCallRecord` has `output=None` and no `inputs` — accepted rather
  than threading messages onto `ProviderError`.
- **Round index resets per `_drive`/feedback re-run** (it is `total_iterations`, a within-run
  counter); the monotonic `sequence` + the ordered file preserve the global story across re-runs.

## Alternatives considered

- **Capture at the orchestrator node only.** Rejected: the agents discard the `ProviderResponse`, so
  the node has the structured output but not the input conversation the ruling requires.
- **Capture at the provider only.** Rejected: the provider is attribution-agnostic about round/outcome
  (those are framework-owned), so the round/routing dimension must come from the nodes.
- **Buffer the trajectory and dump once at finalize.** Rejected: an append-only stream written as each
  round completes is more crash-robust and matches ADR 0039's "always something to read".
- **One consolidated `trajectory.yaml` rewritten per round.** Rejected in favor of append-only JSONL
  (the ruling) — no whole-file rewrite, every line crash-durable.
- **Enable extended thinking to capture raw reasoning now.** Deferred: request-shape + cost change +
  cassette re-record; a separable ADR. The structured "why" satisfies the reconstruction test today.
