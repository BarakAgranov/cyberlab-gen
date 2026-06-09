# 0070 — `completeness_score` is marked non-authoritative (an LLM self-report)

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch A, item ①.4)
**Architecture refs:** `architecture.md §1.5` (the framework owns framework facts; the LLM owns
content/self-assessments), `agents.md §5.4` (the mechanical *definition* of a completeness score),
`agents.md §5.5` (the Extractor-Jury's 0.7-floored `completeness` rubric dimension — the real gate).
Source: investigation `0003 §4-B`, `0004 §1.5/§5` (S12).

## Context

`ExtractionMetadataBlock.completeness_score` is a bare LLM-authored `float`. A repo-wide search finds
it **only at its declaration**: zero framework writer, zero framework consumer, no `0.5` ship gate —
even though `agents.md §5.4` defines a completeness score *mechanically* ("the fraction of content
fields populated with non-`unknown_from_blog` provenance; default floor 0.5"). It is the same
false-provenance *family* as the model-provenance fields (ADR 0065): a number the schema presents
without marking who authored it.

Investigation `0004 §5` **refuted** the stronger claim that an eval test blesses it as truth: the
eval harness computes its own `structural_completeness` separately (`eval/runner/metrics.py`) and
records the LLM's `completeness_score` *alongside* it for comparison, explicitly "independent of the
agent self-score." So there is **no live consumer** treating it as a framework fact — this is a smell
to close, not an active corruption (low live risk).

## Decision

**Mark it explicitly non-authoritative — keep it as the LLM's self-report.** The field's
authorship is made explicit on the field `description` and the `ExtractionMetadataBlock` docstring:
`completeness_score` (with `unknown_fields` / `citations_count` / `notes_for_planner`) is the
Extractor's **own self-assessment**, *not* a framework-computed fact and *not* a ship gate. The
framework never stamps, gates, or consumes it; the substantive completeness gate is the
Extractor-Jury's 0.7-floored `completeness` rubric dimension (`agents.md §5.5`).

This is pinned two ways: the field `description` carries the "self-report / non-authoritative"
marker (asserted by a test so it can't be silently dropped), and a regression guard asserts that
`stamp_framework_provenance` — which stamps `model` and `spec_version` — leaves `completeness_score`
untouched.

## Alternatives considered

- **Framework-compute it per `agents.md §5.4`** (fraction of non-`unknown_from_blog` content fields)
  and stamp it like `model` — rejected. It would convert the eval harness's "agent self-score" into
  a framework fact, entangling the eval's semantics and its `test_record_reads_extractor_self_score`
  framing (the harness deliberately keeps the agent self-score distinct from its own
  `structural_completeness`). The low live risk does not justify reshaping the eval contract.
- **Remove the field** — rejected. It breaks the eval reader and the persisted YAML, and the LLM's
  self-assessment is a legitimately useful comparison signal kept beside the harness metric.

## Consequences

- `completeness_score` is documented as a non-authoritative LLM self-report; nothing in the framework
  treats it as a computed fact (regression-guarded: `stamp_framework_provenance` preserves it; the
  field-description marker is asserted).
- The Extractor-Jury's `completeness` rubric dimension (ADR 0067's floor backstop) remains the real,
  framework-enforced completeness gate.
- **No `docs/` edit** — `agents.md` already separates the mechanical definition from the agent's
  self-report; the schema now states which one this field is. (Doc-improvement note for the next docs
  pass: `schema.md §4.8` could mirror this authorship note.)
