# 0034 — Eval failure scope: skip-this-blog vs abort-the-whole-run (+ `--blog`)

**Date:** 2026-06-05
**Phase:** 1 (provider-backed eval hardening)
**Architecture refs:** `eval.md §7.4`/`§7.6` (per-blog runs), `provider-interface.md §6` (error semantics), `pipeline.md §3.7` (transient retry), ADR 0030 (eval spend guards / fail-fast — **amended**), ADR 0032 (no-progress bail), ADR 0033 (`EmitTruncated`). Classification confirmed with the user.

## Context

Two eval-runner gaps.

**(1) Fail-fast was too coarse.** ADR 0030's fail-fast aborts the *whole run* after N consecutive identical non-retryable failures. But most non-retryable failures are *blog-specific*: the Sysdig blog truncates (ADR 0033 `EmitTruncated`) because of *its* size, which says nothing about the next blog. Aborting everything means a later blog that would extract fine never gets a turn. Meanwhile some failures genuinely should stop everything (auth, quota, no served model) — the next blog will fail identically.

**(2) No way to run one blog.** The CLI took no arguments (`del argv`), so running a single blog meant hand-editing the manifest. With ADR 0033 the cheap-diagnostic workflow is "run one blog, read the shipped spec" — that needs a flag.

## Decision

### Failure scope: three kinds, two outcomes

`BlogRunRecord.failure_kind` gains a scope distinction (replacing the binary `retryable`/`non_retryable`):

| kind | examples | outcome |
|---|---|---|
| `retryable` | `TransientFailure` (timeout/429/5xx/connection after retries) | a blip — never aborts/skips; resets the within-blog counter |
| `blog_fatal` | `EmitTruncated`, `MalformedOutput`, `AgentFailure`, `ExtractionError`, `ToolLoopError`, `ValidationError`/`JuryRejectionError`, ingestion `UnreachableUrlError`/`PaywallError`/`BotDetectedError`, a content/size 4xx `HardFailure` (400/413/422) | **skip this blog**: after `abort_after_consecutive_failures` consecutive identical ones, stop the blog's remaining runs and **continue to the next blog** |
| `global_fatal` | `CapabilityUnreachable` (no served model), `HardFailure` that is auth/permission/payment/model-not-found (401/402/403/404) or has no HTTP status (client-init / missing API key / no-pricing config) | **abort the whole run** on sight (the next blog fails identically); remaining blogs recorded `skipped`, partial archived |

The cost cap still aborts the whole run (spend is global). The within-blog "2 consecutive identical → stop this blog" logic is unchanged in mechanism (ADR 0030/0032 normalization); only its *consequence* changed — stopping a *blog* no longer stops the *run*.

Classification lives in `runner._classify_pipeline_failure(exc)` (eval-runner-only triage), called from `ProviderBackedEvalRunner.run_once` where the exception is live; the run loop in `run_blog_set` routes on the recorded `failure_kind`.

**The two cases the user confirmed:**

- **Generic `HardFailure` — by HTTP status.** `HardFailure` conflates auth/config (global) and content/size 4xx (blog-specific). Resolved by reading the status off the stored `cause` (the vendor `APIStatusError`): 401/402/403/404 or *no status* ⇒ global; 400/413/422 ⇒ blog-specific. Rationale: a no-status `HardFailure` is essentially always client-init/pricing/config (systemic); a blog-specific hard failure essentially always carries a 4xx, and a 400 'request too large' is the truncation-adjacent, blog-size case.
- **Network "provider unreachable" stays `retryable`.** The task listed it as abort-all, but the architecture (`pipeline.md §3.7`, ADR 0030) treats a connection failure as `TransientFailure` — a recoverable blip — and "transient never aborts." Reclassifying it to global would override that contract. Per CLAUDE.md (architecture wins; don't silently override), it stays retryable: a persistent outage fails every blog *cheaply* (no billed tokens, no fabricated success) but still gives each a turn. Escalating persistent-transient-to-abort is a separate, deliberate change to the transient model, not made here. (`CapabilityUnreachable` — "no model in the ranking" — is a config problem, not a network blip, and *is* global.)

### `--blog <id>`

`eval/runner/cli.py` parses a single `--blog <id>` flag (argparse — the CLI previously parsed nothing). It restricts the run to the one curated blog with that id (N times, archived as normal). Unknown id ⇒ exit 2 with the valid curated ids listed. Without the flag, behavior is unchanged (all curated). `run_eval`/`run_blog_set` already accept a `blog_ids` override; the flag threads through it. The `justfile` `eval` recipe gained a variadic `*ARGS` so `just eval --blog <id>` forwards.

## Eval-only scope (explicit)

This skip-vs-abort logic is **eval-runner-only**. A real `extract <url>` run is a single blog with no "next blog" to skip to; the underlying *halt* (truncation, etc.) is universal and already lives in the provider/orchestrator (ADR 0033) and is untouched here. `_classify_pipeline_failure` never re-decides a single blog's fate — only whether the *run* continues to the next blog. No provider, `extract`-verb, or halt behavior changed.

## Alternatives considered

- **Keep abort-all for all non-retryable (status quo)** — rejected: the brief's core complaint; a blog-size truncation starves later blogs.
- **All `HardFailure` = global** (simpler) — rejected by the user: a content/size 400 tied to one blog would abort the whole run, re-introducing the over-aggressive behavior.
- **All `HardFailure` = blog-specific** — rejected: an auth failure would then be retried across every blog instead of stopping immediately.
- **Escalate persistent transient (network-unreachable) to abort** — deferred: overrides the transient-retry contract; out of scope, flagged for a deliberate future decision.
- **Abort the run after 2 *global* failures (like blog-fatal)** — rejected: a global failure is deterministic (auth is auth); aborting on the first saves money and is honest.

## Consequences

- `runner.py`: `FAILURE_NON_RETRYABLE` → `FAILURE_BLOG_FATAL` + `FAILURE_GLOBAL_FATAL`; new `_classify_pipeline_failure` / `_hard_failure_is_global` / `_GLOBAL_HTTP_STATUSES`; `_failure_signature` now keys on `blog_fatal`; `run_blog_set` loop splits global-abort from per-blog stop (within-blog counter resets per blog). `run_once` classifies via the helper.
- `metrics.py`: `BlogRunRecord.failure_kind` doc updated; archived reports carrying the old `"non_retryable"` value still load (extra=ignore str field) — they are read for metrics, never re-run.
- `cli.py`: `--blog` flag (`_parse_args` / `_resolve_selected_blogs`); `run_eval` gains `blog_ids`. `justfile` `eval` recipe takes `*ARGS`.
- Tests: existing fail-fast tests rewritten for skip-vs-abort; new tests for the classifier mapping, blog-fatal-skips-and-continues, first-blog-fails-later-blogs-run, global-aborts-all, and the `--blog` single/unknown paths.
- **Exact command:** `just eval --blog <id>` (e.g. `just eval --blog ai-assisted-aws-intrusion`) — the recipe forwards args; verified `--blog` reaches the CLI. The bare `uv run python -m eval.runner.cli --blog <id>` works too. The no-arg `just eval` is unchanged.
