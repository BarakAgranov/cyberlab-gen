# Agent operating notes for cyberlab-gen

You are working in the `cyberlab-gen` repository. This file tells you how to act here. When this file disagrees with `docs/architecture.md`, the architecture wins; raise the conflict in `dev/decisions/` and proceed with the architecture.

For what cyberlab-gen actually is, read `docs/architecture.md §0.1` (one paragraph). Don't assume from the name.

## Status right now

Phase 0 — skeleton. Provider abstraction, Pydantic schemas, registry loaders, CLI stubs, mock provider, the test harness. **No real generation works yet.** The four CLI verbs exist as stubs that print "not yet implemented." Do not assume any agent (Extractor, Planner, etc.) is callable. Do not write Phase 1+ logic. The phase the repo is in is recorded in the most recent git tag (`v0.x.y`); check it before assuming features exist.

## Build, test, verify

`just verify` is the gate. It runs `ruff check`, `ruff format --check`, `pyright` (strict), and `pytest`. Run it before declaring any task done. CI re-runs it on every push. Other targets in the `justfile`: `just test`, `just lint`, `just format`. Pull exact command behavior from the `justfile` and `pyproject.toml`, not from documentation that describes intent.

## Project map

- `cyberlab_gen/` — the Python package. Subpackages: `cli/`, `framework/`, `agents/`, `schemas/`, `providers/`, `registries/`, `state/`. Cross-subpackage imports go through `__init__.py` re-exports.
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

**Never edit `docs/` as part of an implementation task unless the task explicitly says to.** The docs are the contract; implementation flows from them, not the other way around. If implementation reveals a doc bug, record it in `dev/decisions/` and surface it to the user — don't quietly edit the source of truth.

**Never propose changes that violate `architecture.md §1.5` or `§1.6`.** Bypassing the LLM/framework split, even with good local reasoning, is a contract violation. Same for the no-migration rule (`architecture.md §0.6`) and the cleanup-confidence gate (`architecture.md §0.5` criterion 2).

## Code discipline

The rules below mirror `docs/coding-conventions.md §4` and surrounding sections. If they conflict with the conventions doc, the conventions doc wins — these are visible-here for speed of agent access, not as an independent source of truth.

- Python 3.13+; PEP 695 generic syntax (`class Provenance[T](BaseModel)`); `T | None`, not `Optional[T]`.
- Pyright in strict mode. No `Any` without an inline `# noqa: ANN401` and a justification.
- `extra="forbid"` on every artifact model (Pydantic) — via the `ArtifactModel` base class in `cyberlab_gen/schemas/base.py`. Internal-only types use `InternalModel` with `extra="ignore"`.
- No free text passes between pipeline stages. Every cross-stage boundary is typed.
- Logging uses lazy-format (`logger.info("processing %s", x)`), not f-strings.

For any code style question not covered here, read the relevant section of `coding-conventions.md`. Do not invent conventions.

## Where to write things

- **Architectural questions or design decisions you make during implementation** → `dev/decisions/NNNN-<slug>.md`. 4-digit zero-padded, sequential, ADR template at `coding-conventions.md §7.3`.
- **Per-task execution notes** (what was built, surprises, deferred items) → `dev/phase-N-execution-log.md` for the current phase. Template at the bottom of `dev/phase-briefs/phase-N-agent-brief.md`. Append-only.
- **Code, tests, registries** → under `cyberlab_gen/`, `tests/`, `registry/` per the project map. Match the subpackage layout from `coding-conventions.md §3.1`.
- **Never edit `docs/` from an implementation task.** Surface the issue and let the user route the fix.

## Authority gradient

`docs/architecture.md` > other `docs/*.md` > this file > `dev/decisions/` > inline code comments. If two sources conflict, defer up the chain and record the conflict in `dev/decisions/`.
