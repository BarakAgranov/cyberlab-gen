# 0028 — Provider-backed eval: skip unresolved-URL blogs, archive incrementally, stream progress

**Date:** 2026-06-01
**Phase:** 1 (post-Task-8 follow-up; provider-backed eval hardening)
**Architecture refs:** `eval.md §7.2` (honest framing), `eval.md §7.13` (reports archive to `eval/reports/`), `architecture.md §1.5` (framework routes control flow, not the LLM), ADR 0014 (`TBD` URL sentinel), ADR 0025 (eval-harness Phase-1 shape — **amended**)

## Decision

Three changes to the provider-backed eval run loop, all framework-side and deterministic (`architecture.md §1.5`):

1. **Skip, don't crash, on an unresolved (`TBD`) URL.** `run_blog_set` partitions the blog ids *before* any provider call: a blog whose `url_is_resolved()` is false (the synthetic `long-multi-stage-cloud-campaign` fixture, ADR 0014) is recorded in a new `EvalReport.skipped: list[SkippedBlog]` with reason `"synthetic fixture, no live URL"` and left out of `blog_ids`. The skip is gated on `provider_backed=True` — an offline (fake-runner) run fetches nothing, so it skips nothing and still covers all three curated blogs (the demonstration fixture and the offline smoke test stay honest).

2. **Archive incrementally.** `run_blog_set` gains an optional `on_partial: Callable[[EvalReport], None]` invoked with the report-so-far after each blog completes; `run_eval` passes a closure that re-archives to the same (timestamp-stable) path. A crash on a later blog therefore leaves every completed blog's real result on disk — the expensive `$3.93`-then-crash run would have kept both real blogs' output.

3. **Stream progress to stderr.** A new `EvalProgress` protocol (defined in `runner.py` to avoid an import cycle) with one concrete impl, `eval/runner/progress.py::StderrEvalProgress`, emits one flushed line per event (run start, each run start/finish with verdict + Layer-1 + cost-so-far, each skip, archive path). Progress is stderr-only; the machine-readable summary and the report stay on stdout / disk.

## Context

A real provider-backed run crashed on blog 3 (the `TBD` fixture) because `_build_provider_backed_runner.url_for` raised `ValueError`, and that propagated through `run_once → run_blog_set → run_eval` *before* `archive_report` ran — so ~$3.93 of completed extraction for the two real blogs (Sysdig, Wiz) produced no artifact, and the terminal had shown nothing the whole time. The manifest already carried `BlogEntry.url_is_resolved()` for exactly the skip check; the loader keeps the fixture in the set on purpose (ADR 0014: the manifest is the source of truth for *what's in the set*, not live-fetch readiness), and tests pin its presence, so deleting it from the manifest was the wrong fix.

## Alternatives considered

- **Delete the `TBD` blog from `manifest.yaml`** (the state the working tree was left in) — rejected: it contradicts ADR 0014 and breaks `tests/eval/test_manifest.py` (which requires 3–5 curated blogs incl. the long-blog fixture). The blog belongs in the set; the *run* must tolerate it.
- **Soften `url_for` to return a sentinel instead of raising** — rejected: the runner would then have to special-case a fake URL mid-pipeline. Deciding skip-vs-run up front in `run_blog_set` keeps the control-flow decision in one framework place (`architecture.md §1.5`) and never reaches the provider. `url_for` keeps raising as a defensive backstop.
- **`try/finally` archive of partial results on any exception** (the task's second acceptable option) — equivalent outcome, but incremental archiving is simpler here: the report is rebuilt per blog regardless, so archiving each rebuild needs no separate exception path, and it also gives a live on-disk report during a long run.
- **Progress on stdout** — rejected: stdout carries the final machine-readable summary; mixing live progress in would corrupt it. stderr is the conventional progress channel.

## Consequences

- `EvalReport` gains `skipped: list[SkippedBlog]` (default empty) — **amends the ADR 0025 report shape**. The field defaults empty, so pre-existing archived reports that omit it (e.g. the committed `eval/reports/gen0-20260601T120000Z.yaml` offline fixture) still load unchanged.
- `run_blog_set` / `run_eval` gain optional `on_partial` + `progress` params; all existing callers omit them and are unaffected.
- New module `eval/runner/progress.py`; new protocol `EvalProgress` and class `SkippedBlog` exported from their packages.
- New tests in `tests/eval/test_resilience.py` cover all three: the `TBD` skip (and that offline does *not* skip), the **mid-run-crash partial-archive** invariant, and the stderr progress lines.
- Not exercised by CI's real-provider path (none configured); the run-loop logic is what the tests pin, per `eval.md §7.2`.
