# 0105 — Verify-only jury dead-tool spiral: caller-aware reply, lookup gate, forced terminal emit

**Date:** 2026-06-20
**Phase:** 2 (post-Task-10; surfaced by the architect's first provider-backed `--stage plan` run)
**Architecture refs:** `architecture.md §1.5` (LLM/framework split — tool availability + the tool
loop are framework, deterministic), `§1.6` (mechanical guards are framework, never LLM), `agents.md
§5.5`/`§5.8` (the juries verify `external_api` values; verify-only tool set, ADR 0078), ADR 0042 (an
unavailable enrichment source is never a fatal tool result — the producer "mark unknown and continue"
steer), ADR 0072 (the `ToolUsingAgent` contract), ADR 0101 (external sources are registered but their
live clients are a later config/keys task — i.e. unintegrated this phase), `provider-interface.md §4`
(the tool loop).

## Context — the investigation (run-20260620T190347Z, `--stage plan`, codebuild)

The architect's first provider-backed plan-eval run failed on its own (not the manual stop): run0
ended `blog_fatal`, `halt_reason = "tool-use loop exceeded its request budget without a final
structured output (request_limit of 9)"`. The Phoenix trace (project `cyberlab-gen`) of the failed
`stage.plan_jury` span shows the mechanism exactly: **9 LLM requests, each making one
`external_lookup` TOOL call, never emitting a `JuryVerdict`** — the `request_limit = max_iterations +
1 = 9` (jury `max_tool_iterations = 8`) exhausted entirely on tool calls. The Planner-Jury succeeded
on the smaller initial review (call #2) but spiralled on the larger refinement re-review (call #4).

The 9 calls were **distinct** (not same-args degeneracy): the jury walked its whole source catalog —
`cisa_kev → nvd → mitre_attack → github_api → epss → aws_security_bulletins → osv_dev → github_api →
epss`. Every reply was "external source X is unavailable (registered but not integrated this phase);
… **and continue**" (NVD: "needs a cve_id and none was supplied … and continue"). Three compounding
root causes:

1. **The verify-only tool is a dead stub this phase.** Juries are constructed with **no `nvd_client`**
   (`cli/main.py`), and every other source is unintegrated (ADR 0101), so *every* `external_lookup`
   returns "unavailable" — the jury's only tool can never succeed.
2. **The unavailable reply is producer-oriented.** "Set the field to `unknown_from_blog` … and
   continue" is advice for the Extractor/Planner (who write fields). A verify-only jury cannot set
   fields, and "continue" reads as "try the next source" — so it walks the catalog.
3. **No emit headroom / no exhaustion guard.** The single request budget is shared between lookups
   and the emit; with ≥8 registered sources all replying "continue", the budget is gone before the
   verdict.

(codebuild carries no CVE, so there was genuinely nothing to verify — the jury was guaranteed to find
only dead sources.)

## Decision — three complementary fixes (scope: verify-only agents; part 3 is a standing invariant)

1. **Caller-aware unavailable-source reply** (`ExtractorToolExecutor`). The unavailable replies (the
   non-NVD branch and the NVD no-cve / no-client branches) branch on `self._verify_only`. The
   **producer** wording is unchanged (verified it is still wanted — ADR 0042 needs "mark unknown and
   continue"). A **verify-only** caller is told: "treat the value as unverifiable and proceed to your
   verdict; do NOT try other external sources." Implemented as one `_unavailable_proceed_clause()`
   helper used by all three branches.

2. **Gate `external_lookup` off when there is no verifiable work** (verify-only only). New
   `verify_only_external_lookup_offered(*, nvd_client_wired, spec)` returns true iff an NVD client is
   wired **and** the spec carries ≥1 CVE for it to check — the executor can serve only NVD this phase,
   so that is the honest "is there anything verifiable" predicate. The juries pass the result as
   `offer_external_lookup` through `_emit` → `_build_tools_and_executor` → `extractor_tool_definitions`,
   which returns **no tools** for a verify-only agent with no work (so the jury emits its verdict with
   no tool to spiral on). This **subsumes today's phase-gate** (only NVD is integrated) and **survives
   live-client wiring**: a no-CVE blog like codebuild gets no tool even once sources are live; the
   predicate generalises to "any integrated source has a matching spec value" as more sources gain
   live executor paths. Producers are unaffected (the gate is verify-only; the Planner override keeps
   its full inventory).

3. **Guaranteed terminal emit** (`anthropic_provider`, all tool-using agents). New
   `output_forcing_model_settings(base, request_limit)` returns a per-step `model_settings` **callable**
   that forces `tool_choice=[final_result]` on the last permitted request (`run_step >= request_limit`),
   so a forced-tool loop can never overflow the request budget with zero structured output. A callable
   `model_settings` is pydantic-ai 1.103's supported channel for per-step `tool_choice` (a static
   forced choice raises `UserError`); it resolves to Anthropic `{'type':'tool','name':'final_result'}`.
   This is the **standing invariant** kept even after (1)+(2) remove this particular spiral — it also
   backstops the Extractor and Planner. **Caveat:** tool-forcing requires Anthropic *thinking* OFF
   (`_model_settings` configures none); under thinking pydantic-ai degrades the force to 'auto', so if
   thinking is ever enabled this guard must switch to dropping function tools on the final step.

All three are framework-side (tool availability, reply text, the tool loop) — no change to the
LLM/framework split (`§1.5`/`§1.6`); the LLM still only produces the verdict.

## Out of scope (deliberately untouched)

The jury **8-vs-12 iteration budget**. With the emit reserved (part 3) and the dead tool gated (part
2), the budget is no longer load-bearing for this failure, and the asymmetry may be intentional
(reviewers should explore less than producers). Left as-is unless a post-fix trace shows real
lookup-starvation.

## Consequences

- The Planner-Jury and Extractor-Jury (the spiral is latent in `extract` too — same verify-only tool
  set / budget) can no longer die on a dead-tool spiral: with no verifiable work they are offered no
  tool; when offered, an unavailable source steers them to their verdict; and any forced-tool loop is
  guaranteed a terminal emit.
- **Behaviour change:** verify-only juries are offered `external_lookup` **only** when verification is
  actually possible (client wired + checkable value). Until a verifying client is wired they review
  without it — the honest state (the tool could never succeed before either).
- Tests: caller-aware reply (verify-only vs producer), the gate decision + the no-tool result, and the
  forced-emit settings (unit) + end-to-end (a cooperative `FunctionModel` honours the forced
  `tool_choice` and the loop emits instead of raising `ToolLoopError`). `just verify` green.

## Not part of this ADR (separate follow-ups, recorded for the owner)

The same run surfaced two independent issues, handled separately: (a) the base Planner emits
`produces_world_state[*].identifier_source` as a bare output name instead of `phase_outputs.<name>`
(base-prompt fix; removes a forced refinement round); (b) the one failure was labelled three ways —
`infra_failure` (console), `blog_fatal` (report `failure_kind`), `failed` (run.json `status`) — to be
reconciled (`infra_failure` is the misleading one).
