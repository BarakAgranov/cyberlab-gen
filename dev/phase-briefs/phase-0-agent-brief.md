# Phase 0 — Agent task brief

**Purpose.** Decompose Phase 0 of cyberlab-gen ("the skeleton") into agent-sized tasks. Each task in this brief is something a single coding agent can complete in one focused session, with clear inputs, outputs, and exit criteria. Tasks are sequenced — later tasks depend on earlier ones — but most can be executed by different agents.

**Audience.** Coding agents (Claude Code, Cursor agents, similar) operating in the cyberlab-gen repo. Each task assumes the agent has read this brief plus the listed required documents for that task.

**Authority gradient.** When this brief and an architecture document disagree, the architecture document wins. This brief is the *decomposition* of work the architecture has already specified; it's not a place to make new architectural decisions. If an agent finds a real ambiguity, the agent stops and records the question in `dev/decisions/` rather than guessing.

**Out-of-scope.** This brief is for Phase 0 only. Phase 1 (Extractor + Jury) gets its own brief later, informed by Phase 0 experience. Do not implement Phase 1 logic just because the infrastructure happens to support it.

**Execution log.** Every task ends by appending an entry to `dev/phase-0-execution-log.md`. The file's template is at the bottom of this brief. The log is how Phase 0's lessons feed into Phase 1's brief — without it, future brief-writers (or future agents) reconstruct what happened from git history, which is slow and lossy.

---

## Task 0: Setup (executed once before everything else)

**Goal:** Initialize the repo and verify tooling.

**Required reading:**

- *Primary:* `coding-conventions.md` §1 (philosophy), §2 (tooling stack), §3 (project layout).
- *Cross-references:* `implementation-plan.md` §3.2 (repo structure block); `architecture.md §0.6` (no-migration discipline, which informs the Python pin).

**Inputs:** None (this is the bootstrap).

**Work:**

1. Create the repo layout from `implementation-plan.md §3.2`. Specifically: the top-level directories `cyberlab_gen/`, `registry/`, `eval/`, `docs/`, `dev/`, `tests/`. Each non-leaf directory gets a `.gitkeep` or an `__init__.py` as appropriate.
2. Initialize `pyproject.toml` per `coding-conventions.md §1.1` and `dev/decisions/0003-python-upper-bound.md` (current value: `requires-python = ">=3.13,<3.15"`). Pin the build system (hatchling or similar — pick one and document in `dev/decisions/0002-build-system.md`).
3. Initialize the Phase 0 dependencies from `coding-conventions.md §10.2`'s "Phase 0 (install at project start)" list. Do not add Phase 1+ dependencies (`pydantic-ai`, `langgraph`, `httpx`) yet.
4. Configure `ruff` and `pyright` per `coding-conventions.md §2`. Pyright in strict mode.
5. Set up `just` with at minimum a `verify` recipe that runs `ruff check`, `ruff format --check`, `pyright`, and `pytest`.
6. Set up CI (GitHub Actions or equivalent) that runs `just verify` on every push.
7. Make the first `dev/decisions/0001-click-vs-typer.md` entry committing to `typer` (per `coding-conventions.md §11`).

**Exit criteria:**

- `just verify` passes on an empty codebase (no Python files yet beyond `__init__.py` stubs).
- CI green on the bootstrap commit.
- `pyproject.toml` declares the Phase 0 dependencies and Python `>=3.13`.
- `dev/decisions/` has entries 0001 and 0002.
- Tag `v0.0.1-setup`.

**Decision discretion the agent has:**

- Build system (hatchling vs. setuptools vs. flit). Pick one, document.
- Just-recipe exact form.
- CI provider if unspecified.

**No discretion on:**

- Python version (3.13).
- Linting/typing tools (ruff, pyright in strict).
- Subpackage layout.

**Output notes:** Append a Task 0 entry to `dev/phase-0-execution-log.md`. Create the file from the template at the bottom of this brief if it doesn't exist yet.

---

## Task 1: Pydantic schema base layer

**Goal:** Implement the foundational Pydantic types every other Phase 0 task depends on.

**Required reading:**

- *Primary:* `schema-details.md` §1 (conventions), §2.1 (primitive types and constrained strings), §2.2 (enums), §2.3 (open-set types).
- *Cross-references:* `coding-conventions.md` §4 (type discipline, especially §4.5 on absent-field patterns and §4.6 on `Any` discipline); `schema.md §4.9` for `ProvenanceSource` semantics (the enum is in schema-details but its meaning is in schema.md).

**Inputs:** Task 0 complete.

**Work:**

1. Create `cyberlab_gen/schemas/base.py` with `ArtifactModel` (`extra="forbid"`) and `InternalModel` (`extra="ignore"`) base classes per `schema-details.md §1`.
2. Create `cyberlab_gen/schemas/primitives.py` with the type aliases and constrained types from `schema-details.md §2.1`: `SnakeName`, `NonEmptyString`, `HttpUrl`, `FacetName`, `TradecraftName`, etc.
3. Create `cyberlab_gen/schemas/enums.py` with the enums from `schema-details.md §2.2`: `Severity`, `DetectionComponent`, `ProvenanceSource`, `ConfidenceSource`, `CitationKind`, etc. Use `StrEnum`.
4. Create `cyberlab_gen/schemas/__init__.py` re-exporting the base classes, primitives, and enums. Cross-subpackage imports go through this `__init__.py`.
5. Write unit tests in `tests/unit/schemas/test_base.py` covering: `ArtifactModel` rejects unknown fields; `InternalModel` ignores them; each enum's values match what `schema-details.md` declares.

**Exit criteria:**

- `tests/unit/schemas/test_base.py` passes.
- `pyright cyberlab_gen/schemas/` returns no errors in strict mode.
- The base layer is imported by no other code yet (it'll be imported by task 2).

**Decision discretion:**

- File organization within `schemas/` (one file vs. multiple — the implementation-plan doesn't pin this).
- Test fixture style.

**No discretion on:**

- Class names — match `schema-details.md` exactly.
- The `extra=` settings on the base classes.
- Enum string values.

**Output notes:** Append a Task 1 entry to `dev/phase-0-execution-log.md`.

---

## Task 2: Pydantic schema envelope (Phase 0 subset)

**Goal:** Implement the Phase-0-needed Pydantic models for the AttackSpec envelope. Inner blocks are stubs; the envelope is complete.

**Required reading:**

- *Primary:* `schema-details.md` §3 (Provenance and Citation), §5.1 (AttackSpec envelope).
- *Cross-references:* `schema.md §4.8` (AttackSpec semantics, including `extraction_outcome`), `§4.9` (Provenance semantics, including the discrepancy-with-blog record), `§4.16` (proposal lifecycle, relevant for understanding why `extras` is the escape hatch); `implementation-plan.md §3.2` for the `IngestionResult` field set.

**Inputs:** Task 1 complete.

**Work:**

1. Create `cyberlab_gen/schemas/provenance.py` with `Provenance[T]` (PEP 695 generic), `CitationBlock`, the model_validator for source rules. Implement exactly the shape in `schema-details.md §3`.
2. Create `cyberlab_gen/schemas/attack_spec.py` with the `AttackSpec` envelope per `schema-details.md §5.1` (top-level fields: `spec_version`, `spec_kind`, `source`, `extraction_outcome`, `extras` placeholder). Inner content blocks (chain, thesis, real_world_incidents, etc.) are referenced as `Any` or `dict` stubs for Phase 0 — they get fleshed out in Phase 1. Document each stub with a `# TODO(phase-1)` comment naming the schema-details section that will fill it in.
3. Create `cyberlab_gen/schemas/ingestion.py` with `IngestionResult` per `implementation-plan.md §3.2` (URL, canonical URL, content hash, fetched_at, fetch_method, word_count, publisher_domain, cached_path).
4. Update `cyberlab_gen/schemas/__init__.py` to re-export the new types.
5. Write tests in `tests/unit/schemas/test_provenance.py` and `test_attack_spec.py` covering: Provenance source-rules validator catches all invalid combinations; AttackSpec parses and serializes round-trip; YAML round-trip preserves field order via ruamel.yaml.

**Exit criteria:**

- All schema tests pass.
- Pyright strict-mode clean for `cyberlab_gen/schemas/`.
- A representative AttackSpec instance can be constructed in Python, serialized to YAML, and parsed back to an equal instance.

**Decision discretion:**

- Test fixture creation helpers.
- Whether to put the stub inner blocks inline or in a separate `_stubs.py`.

**No discretion on:**

- Field names, types, or validator logic — these come from `schema-details.md`.
- The `# TODO(phase-1)` comment convention naming the schema-details section.

**Output notes:** Append a Task 2 entry to `dev/phase-0-execution-log.md`.

---

## Task 3: Registry meta-schemas (Pydantic models for every registry)

**Goal:** Implement Pydantic models for every registry's entries. The registries themselves stay mostly empty in Phase 0; what matters is that the loaders can validate any future entry against its model.

**Required reading:**

- *Primary:* `schema-details.md` §6 (registry meta-schemas, including the `_ExternalSourceEntryBase` private base and the type-split discipline); `registry-details.md` §1 (reading guide) and the entry-shape commentary for each registry.
- *Cross-references:* `schema.md §4.11` (registry hierarchy — why bundled and overlay are separate), `§4.14` (external_data_sources vs. static_catalogs semantic split — the type-split in §6.3 enforces this mechanically), `§4.16` (proposal lifecycle — informs the `OverlayRegistryFile` shape with its separate `entries:` and `proposals:` blocks).

**Inputs:** Tasks 1–2 complete.

**Work:**

1. Create `cyberlab_gen/schemas/registries.py` with the Pydantic models for every registry's entry type: `ValueTypeEntry`, `FacetEntry`, `ExternalDataSourceEntry`, `StaticCatalogEntry`, `ExecutionContextEntry`, `LabCredentialEntry`, plus the supporting types (`ExternalSourceEndpoint`, `ExternalSourceParam`, `CacheConfig`, `DiscrepancyMaterialityRule`, `EnrichmentTrigger`).
2. Implement the `_ExternalSourceEntryBase` private base and the type split between `ExternalDataSourceEntry` and `StaticCatalogEntry` per `schema-details.md §6.3`. The `extra="forbid"` discipline is the mechanical guarantor that an external_data_sources entry can't have static_catalogs-only fields.
3. Implement the per-registry-file Pydantic shapes: `OverlayRegistryFile[E]` and `BundledRegistryFile[E]` from `schema-details.md §6.6`, plus the `ProposalAuditBlock` for overlay files.
4. Validators: `StaticCatalogsRegistry._no_enrichment_triggers` and the parallel `_no_discrepancy_materiality_rules` per `schema-details.md §6.3`.
5. Tests in `tests/unit/schemas/test_registries.py`: every Pydantic model parses an empty `entries:` list; the static-catalogs validators correctly reject external-data-sources-only fields; the overlay file shape's `_proposal_keys_match_entries` validator catches mismatches.

**Exit criteria:**

- All registry-schema tests pass.
- The Pydantic models cover every entry type listed in `registry-details.md`'s reading guide.
- Pyright strict-mode clean.

**Decision discretion:**

- File organization (one big `registries.py` vs. one file per registry).
- Test fixture organization.

**No discretion on:**

- Field names — match `schema-details.md` and `registry-details.md` exactly. Cross-check both before committing.
- The type-split discipline (don't unify `ExternalDataSourceEntry` and `StaticCatalogEntry`).
- The `extra="forbid"` setting on all artifact and registry models.

**Output notes:** Append a Task 3 entry to `dev/phase-0-execution-log.md`.

---

## Task 4: Registry loaders and merge logic

**Goal:** Load registries from disk, merge bundled and overlay, expose a clean API to the rest of the system.

**Required reading:**

- *Primary:* `schema.md §4.11` (registry hierarchy and overlay-wins semantics); `schema-details.md §6.6` (merge accessors and the `OverlayRegistryFile[E]` shape).
- *Cross-references:* `registry-details.md §1` (reading guide) and any per-registry section for the example seed entries; `coding-conventions.md §9.3` (YAML I/O via ruamel.yaml); `implementation-plan.md §3.4` check 4 (the registry-load smoke test).

**Inputs:** Tasks 1–3 complete.

**Work:**

1. Create `cyberlab_gen/registries/__init__.py` exposing `MergedRegistries`.
2. Create `cyberlab_gen/registries/loader.py` with functions to load each registry's YAML file using `ruamel.yaml` and validate against the corresponding Pydantic model. Loader raises a clear `RegistryLoadError` with the offending file path on validation failure.
3. Create `cyberlab_gen/registries/merge.py` implementing the bundled + overlay merge per `schema.md §4.11`: overlay wins on name collisions; the bundled and overlay files are loaded separately and merged into `MergedRegistries` with the accessor signatures from `schema-details.md §6.6` (`value_type(name)`, `facet(name)`, `external_source(id)`, `static_catalog(id)`, `execution_context(name)`, `lab_credential_patterns(platform)`).
4. Bundled registry files at `registry/<registry_name>.yaml` are shipped mostly empty (an `entries: []` list is valid). Add one example entry per registry to validate the meta-schemas work — pick the simplest entries from `registry-details.md` (e.g., `aws_credentials` for value_types, `target:aws` for facets).
5. Integration test in `tests/integration/test_registry_merge.py`: write a temp overlay directory with one entry that overrides a bundled entry, load both, assert overlay wins.
6. The Phase 0 smoke test (per `implementation-plan.md §3.4` check 4): write `tests/integration/test_registry_load.py` that loads every bundled registry file through its Pydantic model and asserts no errors. This is the mechanical guarantor of schema-vs-registry consistency.

**Exit criteria:**

- Registry-load smoke test passes.
- Registry-merge integration test passes.
- The example seed entries validate against their meta-schemas.
- A clear error message appears when a malformed registry file is loaded (tested with a deliberately broken fixture).

**Decision discretion:**

- Exact error message format (must include file path and Pydantic error context).
- How to structure the loader internals.

**No discretion on:**

- Overlay-wins semantics.
- The accessor signatures.
- The seed entries themselves (use what's in `registry-details.md`).

**Output notes:** Append a Task 4 entry to `dev/phase-0-execution-log.md`.

---

## Task 5a: Provider call surface (ABC, types, mock, Anthropic scaffold)

**Goal:** Implement the LLM provider abstraction's "can we make a call?" half — the ABC, all type definitions, the mock provider for tests, and the Anthropic adapter scaffolded with `NotImplementedError`. No real API calls land here; that's Phase 1's first work. The accounting half (cost ledger, ranking, pricing) is Task 5b.

**Required reading:**

- *Primary:* `provider-interface.md` §1 (position in the system), §2 (module layout), §4 (the `Provider` interface — all sub-sections), §6 (errors and retries), §7 (MockProvider), §11 (what the provider deliberately doesn't do), §13 (follow-up adapter constraints).
- *Cross-references:* `pipeline.md §3.5` (provider-abstraction architectural rationale), `§3.7` (provider failure handling); `agents.md §0` (the AgentLabel enum's values).

**Inputs:** Tasks 1–2 complete (provider doesn't depend on registries).

**Work:**

1. Create the `cyberlab_gen/providers/` subpackage per `provider-interface.md §2` module layout.
2. Implement `cyberlab_gen/providers/base.py` with the full §4.1 type set: `CitationKind`, `Message` (with the tool-use surface and role-shape validator), `ToolDefinition`, `ToolCall`, `ToolResult`, `TokenUsage`, `ProviderResponse[T_Output]`, `CapabilityHint`, `AgentLabel`, the `Provider` ABC. Use PEP 695 generic syntax.
3. Implement `cyberlab_gen/providers/errors.py` with the error hierarchy from `provider-interface.md §6.1`: `ProviderError` and subtypes (`TransientFailure`, `HardFailure`, `MalformedOutput`, `ToolLoopError`).
4. Implement `cyberlab_gen/providers/retries.py` with the retry strategy from `provider-interface.md §6`: three attempts on transient, three on malformed output, no auto-fallback across providers.
5. Implement `cyberlab_gen/providers/mock_provider.py` per `provider-interface.md §7`: `name = "mock"`, `register()` for canned responses, `register_default_usage()`, unmatched calls raise `UnmatchedMockCall`. The mock doesn't yet integrate with the cost ledger — that's added in Task 5b. For now, the mock can return a `TokenUsage` instance without `cost_usd` being meaningful (use a placeholder `Decimal("0")`).
6. Implement `cyberlab_gen/providers/anthropic_provider.py` as a scaffold — class structure, the Anthropic SDK import, but the actual `complete()` and `complete_with_tools()` methods raise `NotImplementedError("Phase 1")` for now. The scaffold exists so Phase 1 can fill it in without redesigning.
7. Tests:
    - Unit tests for the `Message` role-shape validator (every invariant from §4.1 is covered).
    - Unit tests for the error hierarchy (instantiation works; the right errors are catchable as their base).
    - Integration test for the mock provider's `complete()` happy path.
    - The Anthropic scaffold imports cleanly and raises `NotImplementedError("Phase 1")` when called (test asserts the error and the message).

**Exit criteria:**

- All Task 5a tests pass.
- The mock provider returns parsed structured output for at least one test case.
- The Anthropic scaffold imports cleanly and raises `NotImplementedError("Phase 1")` when called.
- Pyright strict-mode clean for `cyberlab_gen/providers/` (excluding files added in Task 5b).

**Decision discretion:**

- Internal organization of retry logic.
- Exact `UnmatchedMockCall` error message.

**No discretion on:**

- The `Provider` ABC method signatures (these are locked per `provider-interface.md §4.1`).
- The deliberate omission of `temperature`, `top_p`, `top_k` from the interface.
- The `Message` shape (including the role-shape validator).
- The full ten-value `AgentLabel` enum (per `agents.md §0` and `provider-interface.md §4.1`).

**Output notes:** Append a Task 5a entry to `dev/phase-0-execution-log.md`.

---

## Task 5b: Cost accounting (ledger, ranking, pricing)

**Goal:** Implement the LLM provider abstraction's "can we account for it?" half — cost ledger, capability-to-model resolution, the pricing coverage smoke test that catches drift between `model_rankings.yaml` and `pricing.yaml`.

**Required reading:**

- *Primary:* `provider-interface.md` §3 (capability hints and resolution), §5 (cost tracking).
- *Cross-references:* `pipeline.md §3.5` (per-model cost tracking rationale), `§3.6.3` (run report cost block); `provider-interface.md §13.4` (Phase 0 smoke test for pricing coverage); `implementation-plan.md §3.4` check 5.

**Inputs:** Task 5a complete.

**Work:**

1. Implement `cyberlab_gen/providers/cost_ledger.py` with `CostLedger`, `CostLedgerEntry`, `CallOutcome`, `CostReportBlock` per `provider-interface.md §5`. Per-attempt entries (not per-logical-call); the `CallOutcome` enum distinguishes `SUCCESS` from `FAILED` so the eval harness can compute retry rates.
2. Implement `cyberlab_gen/providers/ranking.py` with `ProviderRegistry` (the resolver) per `provider-interface.md §3` and §9. The resolver: a capability hint with no configured provider raises at startup; configured-but-failed calls are `HardFailure`, not silent fallback to another provider.
3. Ship `cyberlab_gen/providers/model_rankings.yaml` with Anthropic-only entries for the three capability hints, using current Anthropic model strings per `provider-interface.md §3.3`. Do not fill in OpenAI entries — leave them as `<pinned-in-release>` placeholders per `pipeline.md §3.5`.
4. Ship `cyberlab_gen/providers/pricing.yaml` with current Anthropic prices per `provider-interface.md §5.2` (Opus 4.7 input 5.00 / output 25.00 / cache_read 0.50 / cache_write_5min 6.25 / cache_write_1h 10.00; other model lines as needed).
5. Update the mock provider from Task 5a to populate `TokenUsage.cost_usd` correctly via the cost-ledger pricing table (the placeholder `Decimal("0")` from Task 5a is now replaced).
6. Wire `--max-llm-cost` plumbing into a `CostLedger.cap_usd` field; the CLI hookup itself lands in Task 7, but the ledger should accept the cap here.
7. Tests:
    - Unit tests for the cost ledger arithmetic (per-attempt entries sum correctly; `by_agent()` and `by_model()` produce correct rollups; `CallOutcome` distinguishes success-after-retry from final-failure; `cap_usd` triggers `BudgetExceeded` at the right threshold).
    - Unit tests for the resolver (capability hint with no configured provider raises; configured-but-failed call is hard failure, not fallback).
    - **Smoke test** per `implementation-plan.md §3.4` check 5: every `(provider, model)` pair in `model_rankings.yaml` has a corresponding entry in `pricing.yaml`. This is one of the two Phase 0 mechanical-consistency guarantors (Task 4 has the other).

**Exit criteria:**

- All Task 5b tests pass.
- Pricing-coverage smoke test passes.
- The mock provider now reports realistic `cost_usd` values populated from the pricing table.
- Pyright strict-mode clean for the whole `cyberlab_gen/providers/` subpackage.

**Decision discretion:**

- Exact `BudgetExceeded` error message.
- How to format the cost-report block for human reading.

**No discretion on:**

- Per-attempt cost-ledger entries (not per-logical-call). A retry-after-failure logical call produces multiple entries.
- The pricing values (current Anthropic published prices; verify against `provider-interface.md §5.2`).
- Keeping OpenAI entries as `<pinned-in-release>` placeholders.

**Output notes:** Append a Task 5b entry to `dev/phase-0-execution-log.md`.

---

## Task 6: Local state management

**Goal:** Implement the `LocalState` class that manages the on-disk layout under `~/.cyberlab-gen/`.

**Required reading:**

- *Primary:* `architecture.md §2.2` (local state); `pipeline.md §3.6` (telemetry directory layout).
- *Cross-references:* `coding-conventions.md §9` (paths via `pathlib.Path`, YAML via ruamel.yaml).

**Inputs:** Task 0 complete.

**Work:**

1. Create `cyberlab_gen/state/__init__.py` exposing `LocalState`.
2. Create `cyberlab_gen/state/local_state.py` with the `LocalState` class. It knows the canonical paths for:
   - `~/.cyberlab-gen/config.yaml`
   - `~/.cyberlab-gen/cache/<content-hash>/`
   - `~/.cyberlab-gen/runs/<run-id>/`
   - `~/.cyberlab-gen/reports/`
   - `~/.cyberlab-gen/registry-overlay/`
   
   Use `platformdirs` so the paths are correct on macOS, Linux, and Windows.
3. Implement directory creation on demand (call `ensure_<dir>()` methods that create the path if missing).
4. Implement a default `config.yaml` shape (empty user config, just the file existing). Use Pydantic for the config model.
5. Tests in `tests/integration/test_local_state.py`: on a fresh tempdir, all directories are created correctly; the config loader returns defaults when the file is missing; loading an existing config preserves all values.

**Exit criteria:**

- `LocalState` directory layout creates correctly on a fresh machine (verify on the agent's local machine, not just in tests).
- Config round-trip works.
- All `state/` tests pass.

**Decision discretion:**

- The default `config.yaml` shape (start minimal; add fields as later phases need them).
- Directory permissions (default user permissions are fine unless there's a reason).

**No discretion on:**

- Path layout under `~/.cyberlab-gen/`.
- Use of `platformdirs` (not hand-rolled path joining).

**Output notes:** Append a Task 6 entry to `dev/phase-0-execution-log.md`.

---

## Task 7: CLI scaffolding

**Goal:** Implement the four CLI verbs as stubs that return "not yet implemented" with appropriate exit codes.

**Required reading:**

- *Primary:* `architecture.md §2.1` (CLI surface — the four verbs).
- *Cross-references:* `coding-conventions.md §6.3` (user-facing output); `provider-interface.md §5` for the `CostLedger.cap_usd` plumbing behind `--max-llm-cost`.

**Inputs:** Tasks 5a, 5b, and 6 complete (CLI needs the provider abstraction for `--max-llm-cost` plumbing and local state for the `--state-dir` override).

**Work:**

1. Create `cyberlab_gen/cli/__init__.py` exposing the `main` entry point.
2. Create `cyberlab_gen/cli/main.py` with `typer`-based CLI declaring the four verbs:
   - `cyberlab-gen generate <url> [--max-llm-cost USD] [--auto] ...` → prints "not yet implemented in Phase 0; this verb lands in Phase 5 (full integrated generation)" and exits 1.
   - `cyberlab-gen fix <lab-dir>` → same template message.
   - `cyberlab-gen validate <lab-dir>` → same.
   - `cyberlab-gen telemetry submit` → same.
   - `cyberlab-gen --version` → prints `0.0.1` and exits 0.
3. Create `cyberlab_gen/cli/output.py` (the submodule from `coding-conventions.md §6.3`) with helpers for formatting clean error messages vs. stack traces. Phase 0's use is minimal (the not-implemented messages); the scaffolding exists for later.
4. Wire up `--max-llm-cost` as a global option that creates a `CostLedger` with the cap. Even though no real generation happens, the cost ledger surfaces in tests.
5. Register `cyberlab-gen` as a console script in `pyproject.toml`.
6. Tests in `tests/integration/test_cli.py`: each verb returns the expected exit code; `--version` works; `--help` works.

**Exit criteria:**

- `cyberlab-gen --version` works on the agent's machine (not just in tests).
- All four verbs return the expected "not yet implemented" message with exit code 1.
- `--help` produces useful output.
- CLI integration tests pass.

**Decision discretion:**

- Exact wording of the not-implemented messages (but each must name the phase where the verb lands).
- Help-text wording.

**No discretion on:**

- The four verbs (must match `architecture.md §2.1`).
- `--version` returns `0.0.1`.
- `typer` as the CLI framework (per `dev/decisions/0001`).

**Output notes:** Append a Task 7 entry to `dev/phase-0-execution-log.md`.

---

## Task 8: Curated blog walks (scaffolding only; human picks blogs)

**Goal:** Three curated blogs are checked in to `eval/blog-sets/manifest.yaml` with manual readings in `dev/curated-blog-walks/`.

**Required reading:**

- *Primary:* `eval.md` §2 (blog curation criteria) and §3 (manifest shape); `implementation-plan.md §3.2` (the three required shapes — AWS TTP, supply-chain, incident-analysis).
- *Cross-references:* `schema.md §4.4`–§4.8 (so the walk template captures the right structural elements: chain steps, facets, value types, lab class).

**Inputs:** Task 0 complete (the `eval/` directory exists).

**Note:** This task involves judgment about blog content; an agent can scaffold but the human collaborator picks the three blogs. The agent's job is preparation and structure, not picking.

**Work:**

1. Create `eval/blog-sets/manifest.yaml` shape per `eval.md`. Include placeholders for three blog entries: `<aws-ttp-blog>`, `<supply-chain-blog>`, `<incident-analysis-blog>`.
2. Create `dev/curated-blog-walks/template.md` — the structure for a manual blog walk: URL, accessed date, summary, chain steps, value types, facets, expected lab class, manual ground truth notes.
3. Stop and surface to the user: "Three blogs need to be picked and read manually before Phase 1. I've created the structure. The picking is yours."

**Exit criteria:**

- The manifest skeleton exists.
- The walk template exists.
- The agent has handed back to the user with a clear summary of what's needed (three blogs picked, three walks written).

**Decision discretion:**

- The walk template structure (consult `eval.md` for what to include).

**No discretion on:**

- Picking blogs. The agent doesn't pick; the user does.
- Inventing blog walks. The walks are real readings of real blogs.

**Output notes:** Append a Task 8 entry to `dev/phase-0-execution-log.md` noting the handoff to the user (template created; awaiting blog picks).

---

## Task 9: README and CONTRIBUTING

**Goal:** A `README.md` appropriate for v0.0 and a `CONTRIBUTING.md` shell.

**Required reading:**

- *Primary:* `architecture.md §0` (the one-paragraph summary of what cyberlab-gen is).
- *Cross-references:* All other docs *briefly* (the agent should skim enough to know what's covered where, but not deeply enough to rewrite them).

**Inputs:** Task 0 complete.

**Work:**

1. `README.md` — half-page at most. What the project is (one paragraph), current status (Phase 0, not yet usable), where to read more (`docs/architecture.md`).
2. `CONTRIBUTING.md` — shell pointing to `coding-conventions.md`, the dev-log discipline, the just-runner.

**Exit criteria:**

- `README.md` exists and renders cleanly on GitHub (or wherever the repo lives).
- `CONTRIBUTING.md` exists and links to the relevant docs.

**Decision discretion:**

- Writing style (informational, not marketing).
- Length (target half-page each).

**No discretion on:**

- Don't claim the project does more than it currently does. Phase 0 is honest about being a skeleton.

**Output notes:** Append a Task 9 entry to `dev/phase-0-execution-log.md`.

---

## Final integration check (the human or a final agent runs this)

After tasks 0–9 are complete, run the **Phase 0 acceptance check from `implementation-plan.md §3.6`** and confirm each item passes. That section is the single source of truth for Phase 0 done-ness; this brief deliberately does not duplicate the criteria, so if §3.6 evolves, the check evolves with it.

If all green, tag `v0.1` and Phase 0 is complete. Move to Phase 1.

---

## Sequencing summary

```
Task 0 (setup)
   ↓
   ├── Task 1 (base layer)
   │     ↓
   │     ├── Task 2 (envelope)
   │     ├── Task 3 (registry models) ──→ Task 4 (registry loaders)
   │     └── Task 5a (provider call surface) ──→ Task 5b (cost + ranking)
   │
   └── Task 6 (local state)            ←─ depends only on Task 0
         ↓
       (Task 5b and Task 6 both feed Task 7)
         ↓
       Task 7 (CLI)
       
Task 8 (blog walks)  ←─ can run in parallel with everything after Task 0
Task 9 (README)      ←─ can run any time after Task 0
```

Tasks 2, 3, 5a, 6 can run in parallel after Task 1 (and Task 6 only needs Task 0). Task 4 needs 3. Task 5b needs 5a. Task 7 needs 5b and 6. Tasks 8 and 9 are independent of the implementation tasks.

For a single-agent execution, a sensible linear sequence is 0 → 1 → 2 → 3 → 4 → 5a → 5b → 6 → 7 → 8 → 9. For multi-agent parallel execution, the dependency graph above is the constraint.

---

## What's intentionally not in this brief

- Prompt engineering. Phase 1 work.
- Validator rules. Phase 1+ work.
- Agent task briefs for Phases 1+. Written after Phase 0 experience.
- Eval harness implementation. Phase 1 starts it; Phase 0 just sets up the directories.
- Telemetry submission. Phase 5 work.
- Refinement loop. Phase 4 work.

If an agent is unsure whether something belongs in Phase 0, the default is: it doesn't. Phase 0 is deliberately small. The implementation-plan's framing applies: "every shortcut here compounds across every subsequent phase," but every premature addition compounds too.

---

## Execution log template

Every task ends by appending an entry to `dev/phase-0-execution-log.md`. The first agent to complete a task creates the file from the template below; subsequent agents append.

```markdown
# Phase 0 execution log

A running record of what each Phase 0 task actually built, what surprised the
implementer, and what was deferred. Entries are append-only; each task's
implementer adds an entry at the end.

The purpose is to inform Phase 1's brief and Phase 1's implementers: where
were the docs ambiguous? what design calls came up that the brief didn't
anticipate? what was harder or easier than expected?

Keep entries terse. Two paragraphs per task is usually right; a long entry
suggests something worth promoting into a `dev/decisions/` ADR instead.

---

## Task <N>: <task name>

**Date:** YYYY-MM-DD
**Implementer:** <agent identifier or human name>
**Time taken:** <rough estimate, e.g., "2 hours">
**Commit:** <git SHA of the final commit for this task>

### What was built

<2–4 sentences. The files created, the tests added, the smoke checks that
pass. Skip what the brief already specified; focus on what the brief didn't.>

### Surprises and friction

<2–4 sentences. Things that took longer than expected, doc ambiguities,
places where the architecture's intent had to be inferred. If a real
question arose, link to the `dev/decisions/` ADR that resolved it.>

### Deferred to later phases

<List anything the implementer noticed but consciously didn't address.
"Phase 1 should X" or "the Anthropic adapter scaffold has a TODO at Y".>

### Doc-improvement notes for the next brief writer

<Optional. What would make the next phase's brief sharper based on what
came up here. This is the feedback loop the reviewer cared about.>

---
```

Two short rules:

- The log is append-only. Never rewrite a prior task's entry. If you discover that an earlier task did something wrong, fix it in code and add a new entry that says so.
- Doc-improvement notes from this log feed directly into Phase 1's brief. Treat them as material for that future document, not as private notes.
