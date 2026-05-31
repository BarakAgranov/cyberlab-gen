# 0019 — Ingestion fetch injection, stdlib HTML normalization, and failure-mode test fixtures

**Date:** 2026-06-01
**Phase:** Phase 1 (Task 3)
**Architecture refs:** `pipeline.md §3.2.1`, `pipeline.md §3.7`, `implementation-plan.md §4.2`, `coding-conventions.md §8.4`, CLAUDE.md hard rules

## Context

Task 3's brief mandates three things that interact awkwardly with a sandboxed,
deterministic CI:

1. "Use recorded HTTP fixtures (`pytest-recording`/VCR) — record once, replay
   forever, cassettes checked in" (`coding-conventions.md §8.4`).
2. The three failure modes (unreachable, paywall via HTTP 403 / very-short
   body, bot-detection via Cloudflare interstitial) must each be tested.
3. Paywalls and bot detection must **never** be bypassed (CLAUDE.md hard rule).

VCR cassettes record *real* server responses. The happy path can be recorded
once against a stable real URL (`https://example.com`). The three failure
modes cannot: there is no stable, reliably-reproducible public URL that always
returns a Cloudflare challenge, a 403 paywall, or a short-body stub on demand,
and recording one would couple the test suite to a third party's anti-abuse
behavior — the opposite of "replay forever." Forcing real recordings of bot
walls also edges toward probing anti-automation systems, which the safety rule
disfavors.

A second decision: which HTML→text library. The brief grants discretion. No
third-party HTML library is currently a dependency.

## Decision

1. **Fetch is injectable.** `ingest(url, *, client: httpx.Client | None)`
   accepts an `httpx.Client`. The happy path passes a real client and is
   recorded with `pytest-recording` (cassette `tests/cassettes/`). The three
   failure modes pass a client built on `httpx.MockTransport`, which serves a
   synthetic response (403, short body, Cloudflare interstitial) or raises
   `httpx.ConnectError` (unreachable) deterministically and offline. This keeps
   the happy path "recorded HTTP" as the brief requires while making the
   failure modes hermetic and free of any real anti-automation probing.

2. **HTML normalization uses the stdlib `html.parser.HTMLParser`.** No
   third-party HTML dependency is added. Heading tags emit markdown-style
   markers (`#`..`######`); other block tags emit paragraph breaks;
   `script`/`style`/`head` text is dropped.

3. **`pytest-recording` is added to dev deps; replay-only by default.**
   pytest-recording's default record mode is `none` (replay-only), so CI never
   reaches the network and a missing cassette fails loudly. Re-recording is an
   explicit `--record-mode=once` opt-in after deleting the stale cassette. The
   package `vcr_config` fixture only filters secret/identifying headers; it does
   *not* pin `record_mode` (pinning it there overrides the `--record-mode` CLI
   flag and makes re-recording impossible). Cassettes live in pytest-recording's
   default per-module location, `tests/<pkg>/cassettes/<module>/<test>.yaml`
   (no `vcr_cassette_dir` ini key — that key is unknown to this plugin and only
   emits a warning).

## Alternatives considered

- **Record cassettes for the failure modes too.** Rejected: no stable
  reproducible source; couples tests to third-party WAF behavior; edges toward
  probing bot-detection, which the safety rule disfavors.
- **Hand-author VCR YAML cassettes for the failures.** Rejected: a hand-written
  cassette is just a synthetic response in a more brittle format than
  `MockTransport`; `MockTransport` is the idiomatic httpx test seam and is
  type-checked.
- **Add `beautifulsoup4`/`html2text`.** Deferred: stdlib parsing is sufficient
  for heading-preserving normalization in Phase 1; a heavier library can be
  adopted later if extraction quality demands it (revisit in eval, Task 8).

## Consequences

- The happy-path cassette
  (`tests/unit/framework/cassettes/test_ingestion/test_ingest_records_a_real_blog.yaml`)
  is the only checked-in network recording for ingestion; refreshing it is a
  deliberate delete-and-re-record.
- Failure-mode tests are fully offline and deterministic.
- `_fetch` reuses `providers.retries.TRANSIENT_RETRIES` so the §3.7 backoff
  parameters have a single source of truth.

## Doc-improvement note for the next brief writer

`coding-conventions.md §8.4` frames all recorded-HTTP tests as VCR cassettes.
For failure modes that can't be reliably recorded (bot walls, paywalls), the
convention should bless transport injection (`httpx.MockTransport`) as the
sanctioned hermetic alternative, so future agents don't try to record a
Cloudflare challenge. Not editing docs from an implementation task (CLAUDE.md);
flagging for the architect.

## Supersedes

None.
