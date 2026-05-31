# Phase 1 execution log

A running record of what each Phase 1 task actually built, what surprised the
implementer, and what was deferred. Entries are append-only; each task's
implementer adds an entry at the end. Purpose: inform Phase 2's brief and
surface doc-vs-code drift, exactly as Phase 0's log did.

Keep entries terse. Two paragraphs per task is usually right; a long entry
suggests something worth promoting into a `dev/decisions/` ADR instead.

---

## Task 0: Architect doc edits + un-defer the Phase-0 catalog smoke check

**Date:** 2026-05-31
**Implementer:** Claude (Opus 4.8) coding agent
**Time taken:** ~30 min
**Commit:** Phase 1 Task 0: architect doc edits + un-defer closed-catalog smoke check

### What was built

Applied the two architect doc edits the Phase-0 ADRs flagged. `schema-details.md`:
in §6.6 changed `OverlayRegistryFile.proposals` from `dict[SnakeName, ...]` to
`dict[RegistryKey, ...]` and rewrote the `_entry_key` resolver doc-comment to the
`ENTRY_KEY_FIELD` ClassVar approach the code already uses (ADR 0015); also added the
`RegistryKey = SnakeName | FacetName` alias to §2.1 so the §6.6 annotation resolves.
`implementation-plan.md` §3.4 check 4: dropped the "once those get Pydantic models"
deferral for the five closed catalogs and pointed it at `catalogs.py` (ADR 0016).
Un-deferred the smoke check by adding a parametrized seed-load test to
`tests/unit/schemas/test_catalogs.py` — each `registry/<name>.yaml` is loaded via
`bundled_registry_dir()` and validated against its `catalogs.py` container model
(counts 10/4/4/7/10 per ADR 0016), plus a severity-ordinal coverage test. The
working-tree Phase-1 groundwork (catalogs.py, five seed YAMLs, ADRs 0015/0016,
modified schemas) was inspected for coherence and committed together. `just verify`
green: ruff clean, pyright 0 errors, 321 tests pass (29 in test_catalogs).

### Surprises and friction

The pre-existing `test_catalogs.py` only exercised the models with inline fixtures;
it did not load the bundled seeds, so the "un-defer the smoke check" requirement was
genuinely unmet until this task added the seed-loading parametrized case. Reused the
registry loader's `bundled_registry_dir()` rather than re-deriving the path, keeping
the catalogs aligned with `test_registry_load.py`. The catalogs are deliberately not
in `MergedRegistries` and have no dedicated loader yet (ADR 0016 leaves that to the
first Phase-1 consumer), so the smoke check validates the container model directly
rather than going through a loader — the right shape for read-only closed sets.

### Deferred to later phases

A dedicated catalog loader and its placement relative to `registries/` (ADR 0016
decision point 4) lands with the first Phase-1 consumer that needs `ordinal` /
`validator_support` / `display_name` (Layer 3, Generator, Docs Generator). Not in
scope here.

### Doc-improvement notes for the next brief writer

`docs/registry-details.md §7.2`'s "or inlined in the schema" aside for
`severity_levels` is now resolved (YAML seed + metadata model); a future architect
pass could prune that aside. No other drift surfaced.

---

## Task 2: Provider call surface for agents (capability-hint dispatch)

**Date:** 2026-06-01
**Implementer:** Claude (coding agent)
**Time taken:** ~1 session
**Commit:** Phase 1 Task 2: provider call surface + structural-retry boundary

### What was built

`cyberlab_gen/agents/call_surface.py` (`AgentRunner`: capability-hint dispatch
over the Phase-0 `ProviderRegistry`, `run`/`run_with_tools`/`build_messages`,
PEP-695 generic methods bound to `BaseModel`), `cyberlab_gen/agents/prompts.py`
(base-prompt-plus-overlay loader, lazy), and re-exports in
`cyberlab_gen/agents/__init__.py`. Added `AgentFailure` and `ConfigError` to the
top-level `cyberlab_gen/errors.py` (ADR 0009 single hierarchy). Added Phase-1
deps `pydantic-ai`, `langgraph`, `httpx`; added dev dep `pytest-asyncio` plus
`asyncio_mode = "auto"` (conventions §8.6). Seeded placeholder `prompt.md` for
extractor + extractor_jury so the loader has files. 17 tests under
`tests/unit/agents/` (resolution reachable/unreachable, validated typed object
via MockProvider, structural-retry-then-AgentFailure via a failing provider
double, per-model cost rollup through the Phase-0 ledger, no-model-name guard).
`just verify` green (362 passed).

### Surprises and friction

`provider-interface.md §6.2` and `pipeline.md §3.7` read as contradictory on
which budget the structural-malformed retry counts against; resolved as a
two-layer budget (provider-internal vs. agent-stage) in ADR 0018. The async
tests silently no-opped until `pytest-asyncio` + `asyncio_mode = "auto"` were
added — that config was specified in conventions §8.6 but not yet in
`pyproject.toml`. `MockProvider` never retries, so a dedicated `_FailingProvider`
double was needed to exercise the structural-retry path.

### Deferred to later phases

Concrete Extractor/Jury prompt content (Task 5); wiring `AgentRunner` into the
orchestrator and recording usage into the live `CostLedger` (Task 6).

### Doc-improvement notes for the next brief writer

`provider-interface.md §6.2` and `pipeline.md §3.7` should cross-reference each
other to make the two-layer structural-retry budget explicit (ADR 0018).
Add `pytest-asyncio` + `asyncio_mode = "auto"` to the Phase-1 dependency list in
the brief so the next agent doesn't rediscover the silent-no-op.

---
