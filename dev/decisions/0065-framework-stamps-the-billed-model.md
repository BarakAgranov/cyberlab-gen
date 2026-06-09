# 0065 — The framework stamps the billed model; the LLM never authors model-provenance

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — model-provenance family fix)
**Architecture refs:** `architecture.md §1.5` (LLMs never author framework provenance), `schema.md`
lines 102/308/444/788/793 (`extraction_metadata.model` = "whatever the provider layer resolved";
`proposed_by_model` = "framework-recorded, not agent-authored"). Investigation 0002 §7 (the defect
class, "item E"). Completes the family begun by commit 7e84faa (lineage.model from the ledger).

## Context

Three fields recorded an **LLM-authored** model string as if it were framework provenance — an
`architecture.md §1.5` violation. The shipped Sysdig spec self-reported `model: "claude-sonnet"` while
the cost ledger correctly billed `claude-opus-4-8`:

- **(a) `extraction_metadata.model`** — the Extractor emits the whole `AttackSpec` (including this
  field), so the *model* writes its own model id; nothing overrode it.
- **(b) `proposed_by_model`** (the proposal audit block) — sourced from `result.spec.extraction_metadata.model`
  in `cli/extract.py::_acceptance_context`.
- **(c) `lineage.model`** — already fixed (sourced from `_billed_extractor_model(ledger)`); confirmed
  no regression.

The docs already say these are framework facts (`schema.md` line 793: "The `proposed_by_model` field is
framework-recorded … Agents do not have model self-awareness; they cannot author their own model id.").
The code had drifted from that contract.

## Decision

**One mechanism: the framework reads the billed model from the cost ledger and overrides any
LLM-authored value.** The canonical reader is the existing `_billed_extractor_model(ledger)` (prefers
the last `EXTRACTOR`-labelled entry; falls back to the last billed entry; `None` on an empty ledger).

- **(a)** A new `_stamp_billed_model(spec, ledger)` returns the spec with `extraction_metadata.model`
  surgically `model_copy`-replaced by the billed model. It is applied on **both** persistence paths:
  the production runner stamps `result.spec` (covering the cwd `attack-spec.yaml` write + the run-store
  mirror), and `_persist_from_state` stamps the on-abort partial spec. No-op (keeps the spec value as a
  last-resort fallback) only when nothing is billed yet — the ledger is non-empty in practice.
- **(b)** `_acceptance_context` gains a `ledger` parameter and sources `proposed_by_model =
  _billed_extractor_model(ledger) or <spec self-report>` (the fallback satisfies the required
  `NonEmptyString` on `ProposalAuditBlock.proposed_by_model`).
- **(c)** `lineage.model` continues to read from the ledger (`_populate_lineage`); `_persist_from_state`
  still takes only `extractor_version` (legitimately spec-authored config provenance) from the spec.

**Why an ADR (not silent):** this is an authorship decision. `extraction_metadata.model` lives inside
the LLM-emitted `ExtractionMetadataBlock` with no prior framework write-back, while `schema.md`'s comment
("whatever the provider layer resolved") implies framework authorship — a genuine doc-vs-code ambiguity
the CLAUDE.md rules say must be resolved in `dev/decisions/`, not silently. We resolve it toward
**framework-stamp** (override) rather than removing the field from the schema: removing/optional-ing a
required `NonEmptyString` inside the locked Extractor emit surface (`output_type=AttackSpec`) is a larger,
riskier change; stamping at the persistence seam is minimal and keeps the model's emit unchanged.

## Consequences

- The shipped + mirrored + on-abort `extraction_metadata.model`, `proposed_by_model`, and `lineage.model`
  all reflect the **billed** model; a spec whose LLM-written model disagrees with the ledger → the
  framework value wins (pinned by tests).
- **No `docs/` edit** — `schema.md` lines 102/308/444/788/793 already describe these as framework facts,
  so the code now matches the docs; the resolved ambiguity is recorded here.
- The mechanism is concentrated in `cli/extract.py` (where the ledger lives); the orchestrator/agent
  layers are untouched, preserving the `architecture.md §1.5` split.
