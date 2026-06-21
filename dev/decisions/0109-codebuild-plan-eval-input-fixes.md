# 0109 — Codebuild plan-eval input fixes: `github_actor_id` value_type + Planner confidence-provenance

**Date:** 2026-06-21
**Phase:** 2 (plan-eval input hardening; follow-up to the run-20260621 post-mortem)
**Architecture refs:** ADR 0107 (the run-20260621 post-mortem fixes), `agents.md §5.4`/`§5.7` (Extractor
proposes value_types; Planner never does), `schema.md §4.12` (the `value_types` registry), ADR 0044
(propose→overlay→validate), `schemas/provenance.py` (the `confidence`-required-on-`llm_inference`
rule). The two remaining non-deferred items from the run-20260621 codebuild post-mortem.

## Context

The run-20260621 codebuild plan-eval produced three different outcomes from one input (ship / spiral /
route-back). Two root causes were within reach (the third — wiring the manifest registry-membership
check — is the owned ADR-0099 §6 deferral and is **not** addressed here):

1. **The central flowing value has no registered `value_type`.** The attack's pivot is the GitHub
   numeric actor/user id the attacker "eclipses" with a superstring to bypass an unanchored
   `ACTOR_ID` webhook regex. The committed AttackSpec fixture's Extractor proposed
   `github_personal_access_token` (now in the architect's local overlay) and the facet
   `target:github_actor_id_filter`, but **never proposed `github_actor_id`** — so when the Planner
   types that flow it finds no registered type, and (non-deterministically) papers over it, routes
   back, or spirals. This is the dominant driver of the variance.
2. **The Planner omits `confidence` on `llm_inference` provenance.** `provenance.py` requires
   `confidence` when `source: llm_inference`; the **Extractor** prompt says so explicitly, but the
   **Planner** prompt's "Provenance discipline" section told it to set `source: llm_inference` + cite
   and **never mentioned `confidence`**. The run-2 forced emit failed validation on exactly this (12×
   "confidence is required when source is llm_inference"). ADR 0107's reserved retries now recover it,
   but eliminating the omission removes the trigger.

## Decision

### Part 1 — register `github_actor_id` in the bundled `value_types` registry

Add `github_actor_id` (a GitHub account's immutable numeric actor/user id, distinct from the mutable
login) to `registry/value_types.yaml`, **appended** after `aws_credentials` (so the load smoke test's
`entries[0] == aws_credentials` holds). `platforms: [github]` (the field is an open `list[SnakeName]`,
no closed platform enum). `sensitive: false` (a public account id).

**Where it lives — bundled vs overlay.** The plan-eval is overlay-**read-only** (ADR 0100/0102) and
must work from the committed repo, so the type has to be registered somewhere the eval reads without a
live promotion. The bundled registry is the reproducible choice (it ships with the repo; the eval is
clean on any machine). `github_actor_id` is a general, reusable type (any GitHub-attack lab needs it),
on par with the `aws_credentials` seed. *Alternative considered:* the architect's local overlay (where
`github_personal_access_token` currently lives) — rejected: not repo-reproducible, and not committable.
**Known residual:** `github_personal_access_token` still lives only in the local overlay, so the
committed fixture is not yet fully self-contained on a fresh machine; bundling it too is left to the
architect (avoids a bundled-vs-overlay duplicate-name merge question and is a separate vocabulary
call). The fixture's `notes_for_planner` prose is left as-is — it is guidance, not a gating mechanism;
the Planner discovers the now-registered type via the registry digest (run 1 already intuited the name
`github_actor_id`, it just was not registered).

### Part 2 — Planner prompt requires `confidence` on `llm_inference`

Extend the Planner base prompt's "Provenance discipline" section to require, for every
`llm_inference` provenance the Planner authors, a `confidence` (0–1) and `confidence_source` —
mirroring the Extractor prompt's existing instruction. Pinned by a prompt-content regression test.

## Out of scope

- **Wiring the manifest registry-membership check** (ADR 0099 §6 owned deferral; prerequisite:
  provisional `PendingProposals` resolution; parked at the Phase-3 `generate` Validator). Part 1 fixes
  the *root cause* that defense would have caught for this blog (the central type now resolves, so no
  untyped-central-value ship), so the acute exposure is closed without pulling the deferral forward.
- The production Extractor proposing `github_actor_id` at extraction time (a prompt nudge that only
  matters on a live re-extract, not this committed-fixture eval).

## Consequences

- The Planner can type the central flowing value of the codebuild blog against a real registry entry,
  so the dominant variance driver is removed; the next plan-eval should converge rather than coin-flip.
- The Planner stops omitting `confidence` on `llm_inference`, so its first emit validates first-shot
  (no reliance on the ADR-0107 retry for this mechanical error).
- Tests: the bundled registry resolves `github_actor_id`; the Planner prompt teaches the
  `confidence`-on-`llm_inference` requirement. `just verify` green.
