# cyberlab-gen — Coding Conventions

**Companion to:** `architecture.md` (hub).
**Document scope:** The Python conventions, tooling, and engineering practices that every contributor (human or agent) is expected to follow. Specifies versions, tools, style choices, type discipline, testing conventions, error handling, and documentation conventions. Does not specify product behavior — that lives in `architecture.md` and its companions.

This document is loaded into the context of every agent task that writes or modifies code. When this document and another doc disagree, the architectural doc wins on *what* to build and this doc wins on *how* to build it.

---

## 1. Language and runtime

### 1.1 Python version

The project targets **Python 3.13**, locked in `.python-version` and `pyproject.toml`'s `requires-python = ">=3.13,<3.15"`.

Reason: 3.13 is the most recent stable Python at project start. The PEP 695 generic syntax, improved error messages, and the stabilized `typing.TypeIs` are conveniences worth having. The narrow upper bound means upgrading Python is an explicit decision rather than something a contributor's local environment forces.

### 1.2 No Python 2 anywhere

Self-evident in 2026 but worth stating: no Python-2 compatibility shims — no `six`, no `2to3`, none of the legacy `__future__` flags. The codebase assumes 3.13 syntax and stdlib.

`from __future__ import annotations` (PEP 563, deferred annotation evaluation) is **not** a Python-2 shim and is permitted — use it where a runtime `typing.get_type_hints` consumer needs annotations to resolve at runtime (e.g. LangGraph builds the `PipelineState` schema this way) or where ruff's `TC` rules otherwise force type-only imports into `TYPE_CHECKING` blocks. See ADR 0083.

---

## 2. Project tooling

### 2.1 Package management: `uv`

The project uses **`uv`** (Astral) for environment management, dependency resolution, lockfile generation, and Python-version provisioning.

- `pyproject.toml` is the source of truth for dependencies.
- `uv.lock` is checked into the repo. It is the authoritative lockfile and must be regenerated (`uv lock`) whenever `pyproject.toml`'s dependency set changes.
- `.python-version` is checked in. `uv` reads it and provisions the right interpreter.
- New contributors run `uv sync` to set up. No `pip install -r requirements.txt`, no `poetry install`.
- Scripts run via `uv run <command>`. This auto-creates the venv on first use and never uses a globally-activated Python.

### 2.2 Linting and formatting: `ruff`

The project uses **`ruff`** for linting *and* formatting. One tool, configured in `pyproject.toml` under `[tool.ruff]`.

- Line length: **100**. Comfortable for modern monitors; long enough that Pydantic field definitions and type signatures rarely wrap.
- Format on save in the contributor's editor. Format checked in CI via `ruff format --check`.
- Lint rules enabled: `E`, `F`, `W`, `I` (imports), `N` (naming), `UP` (pyupgrade), `B` (bugbear), `C4` (comprehensions), `SIM` (simplify), `RUF` (ruff-specific), `TCH` (type-checking imports), `PTH` (pathlib over `os.path`), `LOG` (logging), `T20` (no `print`).
- Ignored: `E501` (line length is enforced by formatter, not linter, to avoid disagreement).

The lint config does not enable `D` (pydocstyle) — docstrings are required only on public framework code (§7.1), not on internal helpers.

### 2.3 Type checking: `pyright` in strict mode

The project uses **`pyright`** as the type checker, configured in `pyproject.toml` under `[tool.pyright]`.

- `typeCheckingMode = "strict"`.
- `reportMissingTypeStubs = "warning"` (not error — some third-party libs lack stubs).
- `reportUnknownMemberType = "warning"`.
- `pythonVersion = "3.13"`.
- `include = ["cyberlab_gen", "tests", "eval"]`.

The strict mode is load-bearing. The architecture's `Provenance[T]` envelope, discriminated unions for `spec_kind` and `identifier_kind`, and typed cross-stage boundaries are *enforcement mechanisms*, not decoration. They only enforce if the type checker actually catches violations.

### 2.4 Testing: `pytest`

The project uses **`pytest`** as the test framework. Additional plugins are added as the phases that need them ship:

- **Phase 0:** `pytest` only. Smoke tests on package imports.
- **Phase 1+:** `pytest-cov` for coverage measurement (reported, not enforced as a floor in v1 — coverage targets get locked in Phase 1 per the implementation plan).
- **Phase 1+:** `pytest-recording` (VCR) for tests that hit external services. Record real responses once; replay forever. Cassettes are checked in.
- **Phase 1+:** `pytest-asyncio` for async test functions (some provider calls are async).

Test discovery follows pytest's default: `tests/**/test_*.py`, with classes `Test*` and functions `test_*`.### 2.5 Task runner: `just`

A `justfile` at the repo root defines all common workflows. Contributors run `just <task>` rather than memorizing tool flags.

Minimum recipes:

```
just sync         # uv sync; provision Python and install deps
just verify       # ruff check + ruff format --check + pyright + pytest
just fmt          # ruff format
just lint         # ruff check --fix
just type         # pyright
just test         # pytest
just eval         # run the eval harness against the curated set
just docs         # render any local doc previews
```

`just verify` is the gate every agent task and every PR must pass. Agents are expected to run it before declaring work done; CI re-runs it on every push.

---

## 3. Project layout

The directory layout is specified in `implementation-plan.md §3.2`. This section adds the *conventions* that go with that layout, not the layout itself.

### 3.1 The `cyberlab_gen/` package

- One subpackage per architectural concern: `cli/`, `framework/`, `agents/`, `schemas/`, `providers/`, `registries/`, `state/`.
- Each subpackage has its own `__init__.py` that re-exports its **public surface** — the stable, curated names cross-phase and external consumers (and tests of the public API) should import from. Prefer the package root for those. Direct leaf-module imports across subpackages (`from cyberlab_gen.schemas.attack_spec import AttackSpec`) are acceptable for internal wiring and are the norm in practice. The hard structural constraint is the cycle ban in §3.3, not routing every import through `__init__`. See ADR 0083.
- Each subpackage's `__init__.py` has a module-level docstring (3–6 lines) explaining what the subpackage is for and which architecture sections govern it.

Two architectural concerns don't live under `cyberlab_gen/`:

- **Validation code.** The five validation layers (per `validation.md`) are foundational to the system and ship from Phase 1 onward. Their codebase location isn't pre-pinned — most likely `cyberlab_gen/validators/` with one submodule per layer (`layer1.py`, `layer2.py`, `layer3.py`, `layer5.py`; Layer 4 is v2-deferred), but the decision is recorded in `dev/decisions/` when the first validator ships in Phase 1.
- **Eval harness.** The eval harness lives at the **repo-level** `eval/` directory, not as a subpackage of `cyberlab_gen/`. It's structured as a sibling of the package because it evaluates the system rather than being part of it (per `implementation-plan.md §3.2` repo layout). Imports flow one way: eval imports from `cyberlab_gen`; `cyberlab_gen` never imports from eval.

### 3.2 The `dev/` directory

Anything that helps a contributor reason about the project but isn't shipped to users. Specifically:

- `dev/decisions/` — non-obvious implementation decisions, one file per decision, named `NNNN-short-title.md` (4-digit zero-padded, project-wide sequential starting from `0001`). Each entry records date, decision, context, alternatives considered, and a pointer to the architecture section it implements.
- `dev/curated-blog-walks/` — manual readings of curated blogs (per `implementation-plan.md §3.2`).
- `dev/prompt-iterations/` — Phase 1 working notes; becomes `prompts.md` once Phase 1 exits.

`dev/` is checked into the repo. Contributors are encouraged to read it; it's the project's working memory.

### 3.3 Imports and module boundaries

- Imports are sorted by `ruff` (isort-compatible) into three groups: stdlib, third-party, first-party.
- Circular imports are a structural error. If two modules need each other, one of them is misplaced; resolve by extraction, not by lazy imports.
- Avoid `from x import *`. Imports are explicit.
- `if TYPE_CHECKING:` blocks are an escape hatch, not a default. Use only when (a) breaking a genuine cycle between two stable modules whose dependency direction is one-way at runtime, or (b) deferring imports that are expensive to load and only needed for type annotations. If you reach for `TYPE_CHECKING` during normal development, it's a code smell — the modules are probably misplaced. Keeping each subpackage's dependency direction one-way (§3.1) should make cycles rare in practice.

---

## 4. Type discipline

### 4.1 Annotate everything

Every function parameter, every return type, every module-level constant. The strict pyright config will reject untyped functions. This is intentional and not negotiable.

For local variables, annotate only when pyright cannot infer cleanly. Don't pad every line with redundant annotations.

### 4.2 Pydantic v2 for data shapes

Anything that crosses a stage boundary, gets serialized to YAML, or represents an artifact (AttackSpec, LabManifest, Provenance, run report entries, etc.) is a **Pydantic v2 `BaseModel`**, not a `dataclass`, `TypedDict`, or plain dict.

Reason: Pydantic gives us field validation, discriminated unions, JSON Schema export, and round-trip-stable serialization — all of which the architecture depends on. The cost of `dataclass` plus hand-rolled validation is paid forever; the cost of Pydantic is paid once.

For internal-only structures that never cross a boundary (a small intermediate value passed between two functions in the same module), `dataclass` or a `NamedTuple` is fine. The rule is *structural*: artifacts use Pydantic; internal scratch types use whatever's clearest.

### 4.3 Generic types use PEP 695 syntax

```python
# Yes:
class Provenance[T](BaseModel):
    value: T
    source: ProvenanceSource
    ...

# No (legacy):
T = TypeVar("T")
class Provenance(BaseModel, Generic[T]):
    ...
```

### 4.4 Discriminated unions

When a field can be one of several shapes distinguished by a literal field (`spec_kind`, `identifier_kind`, citation `kind`, etc.), use Pydantic's `Field(discriminator=...)` pattern. This gives clean error messages on shape mismatch and is the architecture's first line of defense against artifact-type confusion.

### 4.5 Absent-field patterns

`T | None` is the canonical "may be absent" notation (PEP 604, Python 3.10+ syntax). `Optional[T]` from `typing` is equivalent but not used in this project — schema-details.md, provider-interface.md, and registry-details.md all use `T | None` consistently. Stick to that.

Three distinct patterns for fields that can be absent. Picking the right one matters for serialization:

**1. Required field that may be `None` as a meaningful value.** The field is always present in serialization; `None` means "explicitly known to be absent" (e.g., a bundled registry entry has `proposed_in_run: None` because no run produced it).

```python
proposed_in_run: str | None
```

**2. Optional field, omit from YAML when `None`.** The field exists structurally but doesn't appear in the YAML when there's nothing to say. Serialization via `model_dump(exclude_none=True)`.

```python
session_token: str | None = None
```

**3. Optional list/dict, omit from YAML when empty.** Empty collections aren't serialized at all (per `schema.md §4.5`: "Omitted entirely when empty rather than serialized as `extras: []`"). Default factory plus `model_dump(exclude_defaults=True)` or equivalent suppression.

```python
extras: list[ExtrasBlock] = Field(default_factory=list)
```

The rule: pattern 1 when absence is structurally meaningful; pattern 2 when absence is the common case worth hiding; pattern 3 for collections where empty-vs-missing should look the same on disk.

### 4.6 No `Any` without justification

`Any` in a type annotation requires inline justification — either a `# noqa: ANN401` (ruff's flake8-annotations rule for disallowed `Any`) with an explanatory comment, or a docstring sentence in nearby code. Pyright strict mode will flag most cases; the convention is that even allowed `Any` is documented.

Example: vendor SDKs sometimes return loosely-typed responses that we validate downstream with Pydantic:

```python
def parse_nvd_response(raw: dict[str, Any]) -> NvdResponse:  # noqa: ANN401
    # NVD's response is dict[str, Any] until validated against NvdResponse.
    # The justification is that the schema check is at the Pydantic boundary.
    return NvdResponse.model_validate(raw)
```

The same discipline applies to type-checker suppressions: `# type: ignore[<code>]` (mypy) and `# pyright: ignore[<rule>]` (pyright) both require an inline justification. Untyped suppressions accumulate technical debt; documented ones stay reviewable.

---

## 5. Naming

### 5.1 Standard PEP 8

`snake_case` for functions, methods, variables, modules. `PascalCase` for classes. `SCREAMING_SNAKE_CASE` for module-level constants. Private members prefixed with `_`.

### 5.2 Pydantic models

Pydantic model classes use `PascalCase` and end in a noun describing what they represent. Architectural names take precedence: `AttackSpec`, `LabManifest`, `PhaseBlock`, `ChainStep`, `Provenance`, `CitationBlock`. These names match the architecture docs (specifically schema-details.md) exactly so cross-references are unambiguous.

Suffix conventions for new models (use these unless the architecture pins a different name):

- `Block` for nested structural units that appear inside a parent artifact (e.g., `PhaseBlock`, `CitationBlock`, `ExtrasBlock`).
- `Entry` for items in a registry (e.g., `ValueTypeEntry`, `FacetEntry`, `ExternalDataSourceEntry`).
- `Step` for ordered units in a sequence (e.g., `ChainStep`, `StepBlock`).
- Plain noun for top-level artifacts (e.g., `AttackSpec`, `LabManifest`, `Provenance`).

Document any new patterns in `dev/decisions/`.

### 5.3 Booleans

Boolean variables and fields start with `is_`, `has_`, `should_`, `can_`, or similar. Exception: when the architecture uses a specific name (e.g., `sensitive`, `first_class`), that name is preserved; this convention applies only to fields not specified by the architecture.

### 5.4 Function naming

Functions that return a value are nouns or noun phrases (`load_registry`, `compute_content_hash`); functions that perform an action without a meaningful return are verbs (`emit_artifact`, `validate_envelope`). Predicates that return `bool` start with `is_` / `has_` / similar.

---

## 6. Errors and logging

### 6.1 Exceptions

- Catch narrow exception types, not bare `except:` or `except Exception:`.
- The project defines its own exception hierarchy in `cyberlab_gen.errors`. Top-level: `CyberlabGenError(Exception)`. Subdivisions follow the architecture's stage boundaries: `IngestionError`, `ExtractionError`, `PlanningError`, `GenerationError`, `ValidationError`, `ProviderError`, `RegistryError`, etc.
- Each error class carries structured context (`stage`, `run_id`, `cause`) so the run report can record what happened.
- `raise X from Y` preserves the cause chain. Use it.

### 6.2 Logging

- The project uses the stdlib `logging` module. No `print` statements outside of `cli/` user-facing output (and `print` itself is banned by ruff rule `T20`; CLI output goes through a thin `cli.output` module).
- Loggers are named by module: `logger = logging.getLogger(__name__)`.
- Log levels: `DEBUG` for verbose internal state, `INFO` for stage transitions, `WARNING` for recoverable issues, `ERROR` for stage failures, `CRITICAL` reserved for unrecoverable runtime errors.
- Log messages use lazy-format style: `logger.info("processing stage %s", stage_name)`, not f-strings. Two reasons: f-strings always evaluate (even when the log level filters the message out, which matters on hot-path DEBUG logs); f-strings also bypass structured-logging adapters that read the format string and arguments separately for downstream consumers.

### 6.3 User-facing errors

Errors that reach the user via the CLI are formatted by `cyberlab_gen.cli.output` (a submodule of the `cli/` subpackage), which knows the difference between a clean error message ("URL unreachable: <url>; check network and try again") and a stack trace (only shown with `--debug`). Internal traces are written to the run's structured report regardless.

---

## 7. Documentation

### 7.1 Docstrings

- **Public framework code** (anything imported from a package's `__init__.py`) has a docstring: one-line summary, blank line, optional details. Use Google-style docstrings for consistency with `pyright`'s parser.
- **Internal helpers**: docstring only when the function's behavior isn't obvious from its name and types.
- Pydantic model fields use `Field(description=...)` for fields whose meaning isn't obvious from the name. The description flows into the generated JSON Schema and is consumed by agent prompts that reference the schema.

### 7.2 Architecture cross-references

Code that implements a non-trivial architectural decision carries a docstring comment pointing to the section that governs it:

```python
class Provenance[T](BaseModel):
    """Wraps a content field with its source and citations.

    Per schema.md §4.9. Every content field in AttackSpec and Manifest
    uses this envelope; structural fields (ids, paths, types) do not.
    """
    ...
```

This makes architecture-to-code traceability mechanical. When an agent task says "implement the X from §4.5," the resulting code should reference §4.5 in a docstring so the next agent reading the file knows where to look.

### 7.3 `dev/decisions/` entries

Any decision the implementer (agent or human) makes that isn't a direct read-off from the architecture lands in `dev/decisions/` as an Architecture Decision Record (ADR). Format:

```markdown
# NNNN — <short title>

**Date:** YYYY-MM-DD
**Phase:** <implementation phase>
**Architecture refs:** <pointers to relevant sections>

## Decision

<one paragraph>

## Context

<why this came up>

## Alternatives considered

- <option A> — rejected because <reason>
- <option B> — rejected because <reason>

## Consequences

<what changes downstream>
```

**Authorship and process.** Whoever makes the decision writes the ADR at the moment they make it — agent during a task, human during review, or reviewer retroactively (only with the original decider's confirmation of the rationale). ADRs are committed alongside the code that depends on them; the user reviews them at PR time, not as a separate gate. There's no documentation role separate from the deciding role.

**Append-only.** Never edit a prior ADR. If a later decision changes course, write a new ADR that names and supersedes the earlier one (`Supersedes: NNNN` in the header). The history of "we did A, then changed to B because C" is the asset; rewriting hides it.

**ADR vs. phase execution log.** Both live in `dev/`, but they answer different questions. The phase execution log (`dev/phase-N-execution-log.md`) is task-by-task and chronological — it records what happened during a phase. ADRs (`dev/decisions/NNNN-<slug>.md`) are decision-by-decision and permanent — they record why a non-obvious choice was made. If a future reader looking for "why X?" would benefit from finding it as its own searchable file, it's an ADR. If it's just "Task 5 took longer than expected because Y," it's a log entry.

**Not ADR-worthy.** Decisions that are direct read-offs from the architecture (implementing `Provenance[T]` per `schema-details.md §3`; using `ruamel.yaml` per `coding-conventions.md §9.3`) don't need ADRs — the architecture is already the record. The ADR is for things the docs deliberately leave open or that the implementer discovers during the work.

The dev-log is the project's working memory. Future agents (and humans) reading the codebase find these entries when they wonder "why was this done this way?"

---

## 8. Testing conventions

### 8.1 Test types

- **Unit tests** (`tests/unit/`) — exercise a single module or class in isolation. Fast (< 100ms per test). No real LLM calls, no real network. Use the mock provider.
- **Integration tests** (`tests/integration/`) — exercise stage interactions, registry merge, end-to-end flows with the mock provider. Slower (a few seconds per test acceptable).
- **Eval tests** (`tests/eval/`) — placeholder until Phase 1. The eval harness lives at `eval/` and is invoked by `just eval`, not by pytest, but a small smoke test under `tests/eval/` verifies the harness starts cleanly.

### 8.2 Test naming

Tests describe behavior, not implementation: `test_extractor_rejects_blog_with_no_chain_steps`, not `test_extractor_line_42`. The test name reads like a sentence about what should be true.

### 8.3 Fixtures

- Project-wide fixtures live in `tests/conftest.py`.
- Subpackage-specific fixtures live in `tests/<subpackage>/conftest.py`.
- The mock provider is a fixture (`mock_provider`) that yields a configured `MockProvider` instance and lets tests register canned responses.

### 8.4 Recorded HTTP

Tests that exercise real external behavior (NVD lookups, GitHub API) use `pytest-recording`. The first run records; subsequent runs replay from `tests/cassettes/`. Cassettes are checked in. When a recorded interaction needs refreshing, the contributor deletes the cassette and re-records with explicit intent.

Cassette location and naming are configured in `[tool.pytest.ini_options]`: `vcr_cassette_dir = "tests/cassettes"` with per-test cassette files named after the test function. Cassette filtering removes API keys and other secrets before checkin (configured via `vcr_config`).

### 8.5 Property-based testing

For schema validation and registry merge logic, use `hypothesis` to generate edge-case inputs. Particularly valuable for the JSON Schema validation surface, where adversarial inputs surface bugs hand-crafted tests miss.

### 8.6 Async tests

Async test functions use `pytest-asyncio` with `asyncio_mode = "auto"` set in `[tool.pytest.ini_options]`. Tests defined with `async def` are auto-detected; no decorator required. Use `pytest.mark.asyncio` only when overriding mode for a specific test (rare).

### 8.7 Injectable seams at live boundaries

Every boundary that crosses a **live LLM provider or the network** gets an injectable seam: define the dependency as a `Protocol` (or accept it as a constructor argument), ship a production implementation, and substitute a test fake or transport double in tests. The surrounding logic must be exercisable offline — no API key, no network, deterministic — so the only thing a real-provider run adds is the provider's own behavior, never first coverage of the logic around it.

This is a hard convention, not a preference: it is why Phase 1's pipeline, extract verb, and eval harness are fully tested without a configured provider. Established precedents to copy:

- **Ingestion** injects an `httpx.Client`; tests drive it with `httpx.MockTransport` (no real fetch). Recorded cassettes (`§8.4`) cover the one real-HTTP happy path. (`dev/decisions/0019-ingestion-fetch-injection-and-failure-fixtures.md`.)
- **Agent call surface** takes a `Provider`; tests use `MockProvider` and a deliberately-failing double for the structural-retry path. (`dev/decisions/0018-agent-call-surface-structural-retry.md`.)
- **`extract` verb** runs behind an `ExtractRunner` Protocol; tests supply a fake runner so every menu/branch is covered without the pipeline. (`dev/decisions/0024-extract-verb-runner-seam-and-interrupt.md`.)
- **Eval harness** runs behind an `EvalPipelineRunner` seam; the harness and metrics are tested offline, and a real provider only swaps in the production runner. (`dev/decisions/0025-eval-harness-phase1-shape.md`.)

A boundary that hits a live provider or the network without such a seam is a reviewable defect: it forces a key/network into the test path and tends to leave the surrounding logic uncovered.

---

## 9. Async, concurrency, IO

### 9.1 Async

Provider calls are async. The agent layer is async. The orchestration layer (LangGraph) is async-aware. The framework is *not* uniformly async — IO that doesn't benefit from concurrency (reading a YAML file from disk) is sync.

Convention: a function is `async def` only when it `await`s something. A function that's `async def` but never `await`s is a refactor target.

### 9.2 Parallelism

Per-phase Generator parallelism (per `pipeline.md §3.2.9`) uses `asyncio.gather` for fan-out over LLM calls. CPU-bound work (rare in this project) uses `concurrent.futures.ProcessPoolExecutor` only when measurement shows it matters.

### 9.3 File IO

- Paths use `pathlib.Path`, never raw strings. Ruff rule `PTH` enforces.
- Reading and writing YAML uses `ruamel.yaml` (preserves comments, ordering, anchors — important for editable registry overlays and the `--edit-in-EDITOR` interrupt path). PyYAML is *not* used in v1; all YAML I/O goes through ruamel.yaml. Future use only where round-trip preservation truly doesn't matter (e.g., one-time-read internal config blobs).
- Writing YAML for user-facing files (AttackSpec, Manifest in interactive mode) uses `ruamel.yaml` with `block_seq_indent=2` and `width=120`. Consistent output across runs matters for diff-friendliness.

---

## 10. Dependencies

### 10.1 Adding a dependency

Adding a runtime dependency is a deliberate act. The threshold:

- The dependency does something we'd otherwise write nontrivial, well-tested code for.
- The dependency is maintained (commits within ~6 months) or stable (mature, infrequently-updated libraries).
- The dependency has a permissive license (MIT, BSD, Apache 2.0). Copyleft is reviewed case-by-case.

Adding a dev dependency (testing, linting, type-checking tools) is lower-friction: if it earns its place in `just verify`, it's worth having.

### 10.2 Project runtime dependencies (staged per phase)

Dependencies are added when the phase that needs them ships, not all at Phase 0. Installing a framework before its first caller exists adds lockfile churn and type-checking surface for no benefit.

**Phase 0 (install at project start):**

- `pydantic` v2 — data shapes. Every Pydantic model in `schemas/` uses it.
- `ruamel.yaml` — YAML with round-trip preservation. Registry loading uses it from Phase 0.
- `click` or `typer` — CLI framework (recommendation in §11). The CLI verbs exist as stubs in Phase 0.
- `rich` — terminal output formatting. The CLI's user-facing output uses it.
- `platformdirs` — `~/.cyberlab-gen` resolution that's correct on macOS and Windows too.
- `anthropic` — Anthropic provider SDK. The provider abstraction wraps it.

**Phase 1 (added when the first agent and orchestrator ship):**

- `pydantic-ai` — typed agent layer (per `pipeline.md §3.1`). First used by the Extractor in Phase 1.
- `langgraph` — stage orchestration (per `pipeline.md §3.1`). First used when the Ingestion → Extractor pipeline lands in Phase 1.
- `httpx` — HTTP client for external_data_sources lookups. First used in Phase 1 when the Extractor's `external_lookup` tool calls real external sources (NVD, etc.). Used directly, not behind another wrapper.

**Phase 3 (added when generation ships):**

Additional dev tooling for validators (`ruff`, `mypy`, `tflint`, etc.) gets containerized for Layer 3 dry-runs. These are container-image dependencies, not Python deps in `pyproject.toml`.

Things that look load-bearing and are deliberately *not* dependencies, ever:

- LangChain — too much indirection for what we need (per `implementation-plan.md §3.2`).
- Multi-agent frameworks (CrewAI, AutoGen) — the architecture is typed-pipeline, not agent-swarm.
- A vector DB — the architecture's docs fit in context; semantic search would be over-engineering.

---

## 11. Things that need explicit decisions before implementation

A few choices are not pre-locked here because reasonable contributors might disagree and either choice works. Each gets a `dev/decisions/` entry when first encountered, after which the codebase is consistent:

- **`click` vs. `typer`.** Both are fine. Recommendation: `typer`. It's built on `click` but generates the CLI surface from type hints, which composes naturally with this project's strict typing discipline. Final decision in `dev/decisions/0001-click-vs-typer.md` at Phase 0 start.
- **`ruff format` `quote-style`** (`double`, `single`, or `preserve`; the default is `double`, which matches PEP 8 and is what most projects pick). Pick once, document.
- **Pydantic `model_config` defaults.** The base classes `ArtifactModel` (`extra="forbid"`) and `InternalModel` (`extra="ignore"`) in `cyberlab_gen/schemas/base.py` pin the defaults (per `schema-details.md §1`). New models inherit from one of these rather than re-specifying `ConfigDict`. Document the choice for any model family that genuinely needs different settings.

---

*End of coding conventions. See `architecture.md` for what to build; this document is how.*
