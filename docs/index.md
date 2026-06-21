# Documentation index

This is the routing table for the cyberlab-gen documentation. Read this first to find which doc answers your question. Each entry points to specific sections — load those, not whole files.

The corpus splits into two layers (per `architecture.md §0.4`): **architectural** docs specify component identity, contracts, and locked decisions; **implementation** docs specify exact shapes, code-level details, and execution sequencing. Implementation work usually needs both layers; "what does X do" questions usually only need the architectural layer.

## Doc inventory

### Architectural (the contract)

- **`architecture.md`** (~530 lines) — hub document. System foundations, component contracts, locked decisions, deferred decisions. Densest entry points: `§1.5` (LLM-vs-framework split — what LLMs do and never do); `§1.6` (validation layers and the Critic boundary); `§5.20` of `agents.md` and `§4.20` of `schema.md`, which architecture.md names as the two densest concentrations of architectural commitment.
- **`pipeline.md`** (~600 lines) — generation pipeline stage-by-stage, cross-stage contracts, fix pipeline, telemetry, provider failure handling. Entry points: `§3.1` (pipeline shape), `§3.2` (the thirteen stages), `§3.4` (fix pipeline), `§3.7` (provider failure semantics).
- **`agents.md`** (~820 lines) — per-agent contracts (inputs, outputs, tools, quality bar) for the ten agents. Entry points: `§5.2` (agent inventory at a glance), `§5.4`–`§5.16` (one section per agent), `§5.18` (tool inventory across agents), `§5.20` (the canonical ownership table — primary owners for every quality concern).
- **`schema.md`** (~960 lines) — the structured artifacts (Manifest, AttackSpec) and the registries. Entry points: `§4.4` (lab manifest envelope), `§4.8` (AttackSpec envelope), `§4.9` (provenance pattern), `§4.16` (registry evolution / proposal lifecycle), `§4.20` (choice discipline — fallback ladders, categorical choices, cumulative selections).
- **`validation.md`** (~470 lines) — the validator's mechanical passes (four active in v1; the real-platform apply pass is v2-deferred but its section is preserved for design continuity), report shape, refinement loop integration. Entry points: `§6.3` (the five passes at a glance, with v1 vs. v2 status), `§6.4`–`§6.8` (one section per pass, in cheap-to-expensive order; the real-platform apply pass is `§6.7`), `§6.10` (refinement loop integration).
- **`eval.md`** (~300 lines) — the eval harness: what it measures, blog set methodology, statistical-variance handling, stopping-strategy comparison. Entry points: `§7.2` (honest-framing of held-out integrity), `§7.4` (mechanical metrics), `§7.7` (stopping-strategy comparison), `§7.11` (CI gates).

### Implementation (the shapes)

- **`schema-details.md`** (~1530 lines) — Pydantic v2 model shapes for every artifact. Entry points: `§1` (conventions, including `ArtifactModel`/`InternalModel` base classes), `§3` (Provenance envelope), `§4` (AttackSpec envelope), `§5` (LabManifest envelope), `§6` (registry meta-schemas, including the type-split discipline).
- **`registry-details.md`** (~2100 lines) — v1 seed entries for every registry. Entry points: `§1` (reading guide and category framing), then one section per registry (`§2` value_types, `§3` facets, `§4` external_data_sources, `§5` static_catalogs, `§6` execution_contexts, `§7` closed bundled-only catalogs).
- **`provider-interface.md`** (~800 lines) — `Provider` ABC, capability hints, cost ledger, retries, mock provider, adapter conventions. Entry points: `§3` (capability hints and resolution), `§4` (the Provider interface — types, methods, design rationale), `§5` (cost tracking), `§6` (retries and failure semantics).
- **`coding-conventions.md`** (~420 lines) — Python conventions, tooling (uv, ruff, pyright), type discipline, testing, errors, dependencies. Entry points: `§2` (tooling), `§4` (type discipline including PEP 695 and `extra="forbid"`), `§10.2` (dependency staging per phase), `§11` (explicit decisions deferred to `dev/decisions/`).
- **`implementation-plan.md`** (~930 lines) — sequenced build plan from Phase 0 to release. Entry points: `§2` (phase model at a glance — a one-screen mental model of the build), then one section per phase (`§3` Phase 0, `§4` Phase 1, `§5` Phase 2, `§6` Phase 3, `§7` Phase 4, `§8` Phase 5, `§9` Phase 6).

## Routing table

If you're answering a question of the form on the left, read the section(s) on the right.

| Question | Read |
|---|---|
| What does agent X do? What are its inputs and outputs? | `agents.md §5.4`–`§5.16` (one section per agent); `agents.md §5.20` (ownership table) for "who checks X." |
| Who is responsible for checking quality concern Y? | `agents.md §5.20` (ownership table). Each row has one primary owner. |
| What does field F on artifact A look like? | `schema.md §4.4` / `§4.8` for the architectural shape; `schema-details.md §4` / `§5` for the exact Pydantic field with validators. |
| How does pipeline stage S route on failure? | `pipeline.md §3.2.<stage>` for the stage; `pipeline.md §3.7` for cross-cutting provider failures; `pipeline.md §3.2.12` for refinement; `validation.md §6.10` for validator-driven routing. |
| What's the difference between retry and refinement? | `architecture.md §1.7`. Retry is structural-flakiness recovery (per-stage); refinement is quality-driven (pipeline-wide). |
| How do I add a new value type, facet, or external source? | `schema.md §4.16` (proposal lifecycle); `registry-details.md` for entry shape; `schema-details.md §6` for the Pydantic model. |
| What does the registry overlay actually look like on disk? | `schema.md §4.11` for the bundled/overlay hierarchy; `schema-details.md §6.6` for the file shape (`OverlayRegistryFile`, `ProposalAuditBlock`). |
| Why does the LLM not do X? Why is X framework code? | `architecture.md §1.5` (the LLM-vs-framework split and rationale); `architecture.md §1.6` for the validation-layer corollary. |
| What's the contract for the LLM provider abstraction? | `provider-interface.md §4` (the `Provider` ABC and types); `pipeline.md §3.5` for the architectural rationale. |
| What gets validated at each pass? | `validation.md §6.4` (static-schema validation) through `§6.8` (safety scans). `§6.3` is the at-a-glance summary; v1 runs four passes (static-schema, semantic cross-check, containerized dry-run, safety scans) — the real-platform apply pass is v2-deferred but its section is preserved for design continuity. |
| What's deferred to v1.5+ or v2? | `architecture.md §8.2` (v1.5+ items); `§8.3` (v2 items); `§8.4` (post-launch calibration). |
| Why was decision D made? | If D is an **architectural** decision (LLM/framework split, schema shape, registry model, validation-layer boundaries, deferrals to v1.5+/v2), check the rationale paragraph in the relevant `§` of `architecture.md`. If D is a **build-tooling** decision (CLI library, build system, lint rules, dev-loop choices), check `dev/decisions/NNNN-<slug>.md` for the ADR. If you can't tell which kind D is, it's architectural until proven otherwise — implementation decisions inherit from architectural ones, never the reverse. |
| Which phase am I in? What lands in this phase? | `implementation-plan.md §2` (phase table); the current phase's `§` (Phase 0 → `§3`, Phase 1 → `§4`, etc.). The latest git tag also indicates current phase. |
| How do I write code in this repo? Style, tools, conventions? | `coding-conventions.md` — `§2` for tooling, `§4` for types, `§8` for testing. |
| Where do I record a design decision I made during implementation? | `dev/decisions/NNNN-<slug>.md` (ADR template at `coding-conventions.md §7.3`). |
| Where do I write per-task execution notes during a phase? | `dev/phase-N-execution-log.md` (template at the bottom of the phase brief). |
| What does the eval harness measure? How do I run it? | `eval.md §7.4` (mechanical metrics); `eval.md §7.7` (stopping-strategy comparison); `eval.md §7.11` (CI gates). |

## Cross-cutting concepts

Terms that appear across multiple docs. Use these pointers as the canonical definition.

- **Provenance** — `schema.md §4.9` is the canonical definition (the per-field envelope with source, citations, confidence, discrepancy record). `schema-details.md §3` is the Pydantic shape with the `_source_rules` validator. The framework-imposed override behavior is in `schema.md §4.9` ("framework-imposed sources") with the actual rewrite happening per `pipeline.md §3.2.4`.
- **AttackSpec vs. LabManifest** — `schema.md §4.2` is the canonical "two artifacts, two roles" framing. Detailed shapes in `schema.md §4.8` (AttackSpec) and `§4.4` (LabManifest). Who produces each is in `agents.md §5.4` (Extractor → AttackSpec) and `§5.7` (Planner → LabManifest).
- **First-class vs. best-effort runtimes** — `architecture.md §0.2` for the v1 set (AWS, Azure, GCP, GitHub are first-class); `schema.md §4.13` for the registry-level facet that encodes the distinction; `validation.md §6.6` for what first-class status means at validation time.
- **Refinement vs. retry** — `architecture.md §1.7` is the canonical distinction (with the comparison table). Retry is in each stage's failure handling; refinement is coordinated by `pipeline.md §3.2.12`.
- **Lab class is emergent** — `architecture.md §0.7` is the canonical principle (lab class is the sum of per-step decisions, not a pre-classification). `schema.md §4.20` is the operational consequence (choice discipline per step).
- **Bundled vs. overlay registry** — `schema.md §4.11` for the read-only-bundled / writable-overlay split; `schema-details.md §6.6` for the file shape and the proposal-audit-separate design.
- **Architecture vs. implementation split** — `architecture.md §0.4` defines the line. Architecture docs specify *what*; implementation docs specify *how*. `architecture.md §8.6` lists every companion implementation doc and its status.
