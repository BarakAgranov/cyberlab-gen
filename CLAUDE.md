# Agent operating notes for cyberlab-gen

You are working in the `cyberlab-gen` repository. This file tells you how to act here. When this file disagrees with `docs/architecture.md`, the architecture wins; raise the conflict in `dev/decisions/` and proceed with the architecture.

For what cyberlab-gen actually is, read `docs/architecture.md §0.1` (one paragraph). Don't assume from the name.

## Status right now

Phase 1 — the front half of the pipeline runs for real against a paid provider. The `extract` verb works end-to-end: Ingestion → Extractor → Layer-1 static-schema validation → Extractor-Jury → persistence, with per-call cost recording, the catastrophe ceiling, the post-Extractor interactive interrupt, and the propose→overlay→validate proposal loop (ADR 0044). Persistence is the run-store, which saves every run on every exit path (ADR 0039); the agent layer runs on pydantic-ai (ADR 0036) and observability is local Phoenix (ADR 0041). **Callable agents today: only the Extractor and the Extractor-Jury.**

**Still stubs:** `generate`, `validate`, `fix`, and `telemetry submit` print a not-implemented message and exit non-zero. The downstream agents — **Planner, the Per-phase / Lab-level / Cleanup / Docs Generators, the Critic, the Repair Agent** — do **not** exist yet. Do not assume any of them is callable, and do not build the `generate` pipeline unless the task explicitly says to. The most recent git tag (`v0.0.1-setup`) is stale and lags the real state by dozens of commits — trust the `dev/` execution logs and ADRs over the tag until it is re-cut.

## Build, test, verify

`just verify` is the gate. It runs `ruff check`, `ruff format --check`, `pyright` (strict), and `pytest`. Run it before declaring any task done. CI re-runs it on every push. Other targets in the `justfile`: `just test`, `just lint`, `just format`. Pull exact command behavior from the `justfile` and `pyproject.toml`, not from documentation that describes intent.

## Project map

- `cyberlab_gen/` — the Python package. Subpackages: `cli/`, `framework/`, `agents/`, `schemas/`, `providers/`, `registries/`, `validators/`, `state/`. Each `__init__.py` re-exports the subpackage's public surface (prefer it for cross-phase/external consumers); direct leaf-module imports across subpackages are fine for internal wiring — the hard rule is no import cycles (`coding-conventions.md §3.3`; amended in ADR 0083). (`validators/` holds the mechanical validation layers — Phase 1 ships Layer 1 as `static_schema_validator.py`; see ADR 0022/0026.)
- `registry/` — bundled YAML registries shipped with the package.
- `eval/` — eval harness and curated blog sets. Top-level, sibling of `cyberlab_gen/`. Not part of the installed package.
- `tests/` — pytest tests: `unit/`, `integration/`, `eval/`.
- `docs/` — architecture and reference docs (read these for context).
- `dev/` — working notes, decision logs (ADRs), per-phase execution logs, prompt iterations. Read this for project history.

## How to use the documentation

`docs/index.md` is the routing table — start there for any question. It maps question types to specific doc sections.

`docs/architecture.md` is the hub. Read its §1.5 (LLM-vs-framework split), `agents.md §5.20` (ownership table), and `schema.md §4.20` (choice discipline) before any non-trivial work — these three are the densest concentrations of architectural commitment in the corpus.

Cross-reference syntax: `file.md §N.N` means open that file and search for the section. References are precise — load the named section, not the whole file.

Architecture-vs-implementation split (per `architecture.md §0.4`): the architecture docs (`architecture.md`, `pipeline.md`, `agents.md`, `schema.md`, `validation.md`, `eval.md`) specify *what* and *contracts*. Implementation docs (`schema-details.md`, `registry-details.md`, `provider-interface.md`, `coding-conventions.md`, `implementation-plan.md`) specify *how* and *exact shapes*. When you're writing code, you need both layers; when you're answering "what does X do," you usually only need the architecture layer.

## Retrieval order

When you need context not already in the conversation:

1. Check `docs/index.md` to identify the right doc(s).
2. Read the named section, not the whole file.
3. Follow cross-references only as needed; do not pre-load.
4. Stop reading when you have enough to act. More context costs context budget; spend it deliberately.

## Hard rules

**LLMs do** (per `architecture.md §1.5`): produce content (extraction, planning, generation, docs) and produce structured judgments (jury verdicts, Critic verdicts, refinement recommendations).

**LLMs never** (per `architecture.md §1.5`): route control flow, decide their own retry budgets, decide whether to stop refinement, modify shared state outside their designated output, override blog content with API content, decide whether their output ships. These are framework code, deterministic and auditable.

**Mechanical safety checks are never LLM-based.** Layer 5 high-severity findings halt the pipeline mechanically; the cleanup-confidence gate is mechanical; credential-pattern matching is mechanical. Per `architecture.md §1.6`.

**Never resolve architectural ambiguities silently.** If you read the docs and something is unclear or under-specified, record the question in `dev/decisions/NNNN-<slug>.md` (ADR style) and either pick the most conservative reading or stop and ask the user. Never invent a contract that isn't in the docs.

**You own `docs/` edits — including architecture-tier — but every edit is deliberate and surfaced, never silent** (ADR 0084). The docs are the contract; implementation flows from them. When a task touches the docs — a doc bug, a needed contract change, a reconciliation — make the edit, record the rationale in `dev/decisions/` for anything substantive (a changed contract, not a typo), and list every doc change explicitly in your summary so the user can verify it. Never change a contract as an unannounced side effect of an implementation edit, and never silently. A change to an `architecture.md §1.5`/`§1.6` invariant (the LLM/framework split, mechanical-safety) still needs an ADR and explicit user sign-off before you rely on it.

**Never propose changes that violate `architecture.md §1.5` or `§1.6`.** Bypassing the LLM/framework split, even with good local reasoning, is a contract violation. Same for the no-migration rule (`architecture.md §0.6`) and the cleanup-confidence gate (`architecture.md §0.5` criterion 2).

**Base class discipline.** `schema-details.md` shows many classes as `BaseModel + ConfigDict(extra="forbid")`. The architectural intent is `ArtifactModel`. See `dev/decisions/0004-base-class-discipline.md` for the rule and the three reserved cases for `BaseModel`.

## Code discipline

The rules below mirror `docs/coding-conventions.md §4` and surrounding sections. If they conflict with the conventions doc, the conventions doc wins — these are visible-here for speed of agent access, not as an independent source of truth.

**Python 3.13+ syntax. Verify your syntax against 3.13, not against training-data defaults.**

- PEP 695 generic syntax: `class Provenance[T](BaseModel)`. Not `Generic[T]` from typing.
- `T | None`, not `Optional[T]`. Not `Union[T, None]`.
- Built-in generics: `list[int]`, `dict[str, int]`, `tuple[int, ...]`. Not `List`, `Dict`, `Tuple` from typing.
- `StrEnum` from `enum`, not custom string-enum classes.

If you're unsure whether syntax is 3.13-current, check `pyproject.toml`'s `requires-python` and confirm. Older Python idioms still parse, but pyright strict will flag them, and they signal "this agent isn't paying attention to the project's actual Python version."

**Other code rules:**

- Pyright in strict mode. No `Any` without an inline `# noqa: ANN401` and a justification.
- `extra="forbid"` on every artifact model (Pydantic) — via the `ArtifactModel` base class in `cyberlab_gen/schemas/base.py`. Internal-only types use `InternalModel` with `extra="ignore"`.
- No free text passes between pipeline stages. Every cross-stage boundary is typed — and *typed* means typed **contents**, not a typed wrapper around stringified data. Structured findings (jury field-level feedback, validator findings) travel between stages in structured form (`field_path`, `problem`, `suggested_fix`); rendering to prompt text happens only at the prompt boundary, with the structured form retained for the framework. This is the prerequisite for targeted-patch refinement (`docs/architecture.md §1.7`).
- Logging uses lazy-format (`logger.info("processing %s", x)`), not f-strings.

**Test discipline.**

- Every behavior the brief claims should work gets a test that fails when the behavior breaks.
- Tests fail meaningfully when the behavior breaks. A test that passes when the code is deleted is worse than no test — write one that demonstrates the behavior.
- For Pydantic models: round-trip serialization, validator behavior on bad input, field-level constraints. Not just "the model can be instantiated."
- For loaders: behavior on missing files, malformed files, and the happy path.
- For the cost ledger (and similar arithmetic): edge cases (zero-cost calls, retries) and rollups, not just sums.
- Smoke tests that guarantee mechanical consistency across files (`tests/integration/test_registry_load.py`, the pricing-vs-ranking coverage test) are first-class. They catch the failure modes the brief explicitly worries about.

If you find yourself writing code without a clear test for it, stop and ask whether the brief expected a test that you missed.

For any code style question not covered here, read the relevant section of `coding-conventions.md`. Do not invent conventions.
## Where to write things

- **Architectural questions or design decisions you make during implementation** → `dev/decisions/NNNN-<slug>.md`. 4-digit zero-padded, sequential, ADR template at `coding-conventions.md §7.3`.
- **Per-task execution notes** (what was built, surprises, deferred items) → `dev/phase-N-execution-log.md` for the current phase. Template at the bottom of `dev/phase-briefs/phase-N-agent-brief.md`. Append-only.
- **Code, tests, registries** → under `cyberlab_gen/`, `tests/`, `registry/` per the project map. Match the subpackage layout from `coding-conventions.md §3.1`.
- **`docs/` edits are yours to make — but surface every one** (ADR 0084). Make the edit, list it in your summary for the user to verify, and record substantive contract changes in `dev/decisions/`. Never change the contract silently.

## Authority gradient

`docs/architecture.md` > other `docs/*.md` > this file > `dev/decisions/` > inline code comments. If two sources conflict, defer up the chain and record the conflict in `dev/decisions/`.
