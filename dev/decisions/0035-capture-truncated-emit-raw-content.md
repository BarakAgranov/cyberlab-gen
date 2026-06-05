# 0035 — Capture a truncated emit's raw partial content for inspection

**Date:** 2026-06-05
**Phase:** 1 (provider-backed eval hardening; "let a maintainer finally READ what the model emits")
**Architecture refs:** `provider-interface.md §6.2` (malformed-output), ADR 0032 (truncation diagnostic + `_dump_emit_on_validation_error`), ADR 0033 (truncation halt / `EmitTruncated`), `implementation-plan.md §4.6` (long-blog risk, unhandled). Builds on ADR 0032/0033.

## Context

Every real blog tested truncates the AttackSpec emit at `max_tokens=16384`
(`stop_reason == "max_tokens"`), raising `EmitTruncated` (ADR 0033). Because the
emit truncates *before* it validates, the partial content is discarded — it is
never written anywhere. So a maintainer has **never been able to read what the
model actually produces**.

That blocks a real design decision: is the model emitting a **tight** spec that is
just genuinely large, or a **bloated** one (verbose descriptions, redundant
tradecraft, over-long `blog_excerpt`s) that would fit if it were disciplined? The
answer decides whether the long-blog fix (P4) is chunked-emit alone or *also* needs
prompt tightening. Right now it is guessed at blind.

The ADR-0032 content dump (`_DEBUG_EMIT_ENV`) writes the raw emit to **stderr**,
which is ephemeral and, during an eval run, interleaved with the progress stream —
not readable after the fact. The gap is a *persisted, readable* artifact.

## Decision

On a **truncated** emit (the existing `truncated` branch of
`_dump_emit_on_validation_error`, keyed on `stop_reason == "max_tokens"`), write the
model's RAW partial tool-call arguments to disk *before* `EmitTruncated` is raised.

- **Gated** behind a new env var `CYBERLAB_GEN_EMIT_DUMP_DIR`, whose value is a
  **directory**. Unset (default) ⇒ nothing written, normal runs stay quiet. Set ⇒
  the dump lands at `<dir>/<schema>-truncated.json` (for the Extractor:
  `<dir>/AttackSpec-truncated.json`). A directory (not a flat on/off) keeps the
  provider package generic — it never hardcodes the eval's `eval/reports/specs/`
  layout; the *caller* points the var there.
- **Content is the raw partial dict** (`emit_input`, the `tool_use.input` the model
  produced — incomplete and schema-invalid by design), under an `emitted_arguments`
  key, beside a `_truncation_dump` header: `schema`, `stop_reason`, `output_tokens`,
  `max_tokens`, and `parse_error` (the first missing field ≈ where it cut off). Full
  content, not ids-only — verbosity/structure/how-far-it-got is the entire point.
  Valid JSON so it is trivially re-loadable.
- **Blog identity:** the provider does not know the blog id (it is never threaded
  through the call surface, and doing so would cross the eval/provider boundary).
  The blog is identifiable from the `source` block *inside* the dumped content; the
  header note says so. For the single-`--blog` diagnostic this exists for, that
  suffices.
- **Filename is fixed per schema** (`<schema>-truncated.json`), overwritten on
  re-run (latest wins) — consistent with how `specs_dir` overwrites
  `<blog>-run<idx>.yaml`. A multi-blog run that truncates more than one blog keeps
  only the last; acceptable because the diagnostic is run single-blog, and the
  written path is logged at WARNING.

## Halt behavior is unchanged (ADR 0033)

This is **purely additive**. The dump is written inside the same `truncated` branch
that already logs the verdict, *before* the existing
`if _is_truncated(response): raise EmitTruncated(...)`. The write is **best-effort**:
wrapped in `try/except OSError`, a failure is logged and swallowed. So the run still
fails fast, still ships nothing, still aborts cheaply — the dump only *additionally*
persists the content. No control flow, retry budget, or `EmitTruncated` raising
changed.

## Alternatives considered

- **Reuse `_DEBUG_EMIT_ENV` (stderr dump)** — rejected: stderr is ephemeral and
  interleaves with eval progress; the gap is a *file* to read after the run.
- **A boolean flag writing to a hardcoded `eval/reports/specs/`** — rejected: the
  provider is a generic package and must not know the eval's directory layout. A
  directory-valued var keeps the layering clean and lets the caller choose.
- **Thread the blog id into the dump filename/header** — rejected: it is not
  available at the provider boundary without adding a parameter to the call surface
  (eval/provider boundary crossing). The `source` block in the content identifies
  the blog.
- **Dump on every failed emit, not just truncation** — rejected (scope): the gap is
  specifically the *discarded truncated* content; a complete-but-invalid emit's
  content is already inspectable via `_DEBUG_EMIT_ENV`, and the `*-truncated.json`
  name would then lie.

## Consequences

- `anthropic_provider.py`: new `_EMIT_DUMP_DIR_ENV` + `_write_truncation_dump`;
  called from the `truncated` branch of `_dump_emit_on_validation_error`. `pathlib`
  import added. No change to the emit-parse sites or the halt.
- New tests: a truncated emit writes `<schema>-truncated.json` with the raw content
  + header; a complete-but-invalid emit writes nothing; the unset env writes
  nothing; end-to-end through `complete()` the dump is written AND `EmitTruncated`
  still raises.
- **How to enable (the intended one-shot diagnostic):** set the dir and run one
  blog. PowerShell:
  `$env:CYBERLAB_GEN_EMIT_DUMP_DIR="eval/reports/specs"; just eval --blog aws-codebuild-actor-id-regex-bypass`
  then read `eval/reports/specs/AttackSpec-truncated.json`.
