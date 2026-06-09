# 0002 — Extractor emit truncation on a dense blog; the per-request ceiling, the reactive-proposal re-extraction, and the model-attribution defect

**Date:** 2026-06-09
**Status:** Findings captured, then implemented. Unlike `dev/investigations/0001`, the
fixes this document recommends were carried out in the same batch (the five-item NOW set
below); the durable class-fix (streaming + sectioned emit) remains deferred. No `docs/`
contract was changed.
**Update (2026-06-09, pre-Phase-2 consolidation batch):** §6's structural lever is now complete —
the **registry digest** is surfaced to the Extractor so B-ii's first-pass proposing is no longer
blind (E1, ADR 0062), and §7's model-attribution defect family is fully fixed (the framework stamps
the billed model on all three instances — provenance family, ADR 0065). **Still deferred:** the
durable truncation class-fix (streaming + sectioned/continuation emit, D1/D2/P4; A-NOW's 20K bump is
the stopgap) and the blog-prefix `CachePoint` (B-i's remaining piece). See the refreshed pre-Phase-2
status block in `phase-1-execution-log.md`.
**Settled principle it feeds:** A-NOW raises a calibration constant (no contract); E restores
the `architecture.md §1.5` provenance rule (billed facts come from the framework, not the
LLM); B-i is recorded as its own ADR (the locked provider surface — `provider-interface.md`).
**Provenance:** reconstructed from the run store
(`~/.cyberlab-gen/runs/20260608T202612Z-www-sysdig-com-ai-assisted-cloud-intrusion-achie/`:
`run.json`, `cost.yaml`, `checkpoint.sqlite` decoded), the persisted `spec.yaml`, the cached
normalized blog, and a read of the source. Each claim is tagged `[V]` (verified in an
artifact/code I read) or `[I]` (inferred). Nothing in this document re-derives at read time;
it is the capture so future sessions reference a repo document instead of the run store.

## Why this exists

A real `--auto extract` on the Sysdig "AI-assisted cloud intrusion achieves admin access in 8
minutes" blog ran **2 LLM calls**, spent **$2.93**, and exited `status: failed` with
`halt_reason` = *"emit truncated at the output-token limit (finish_reason='length',
max_tokens=16384)…"*. The partial run **did** persist a complete `spec.yaml`. The CodeBuild
blog (investigation 0001 / ADR 0058) had shipped clean the run before — so the ADR-0058
category fixes are sound; this is a **different, harder boundary**: the spec is large enough
that the atomic AttackSpec emit hits the per-request output ceiling.

Two data points looked contradictory and had to be reconciled before any fix:

1. Both calls report output tokens **far above** 16,384 (31,701 and 38,249) yet the halt says
   `max_tokens=16384`. Where is the real ceiling, and why does the message say 16384?
2. Input nearly **doubled** between calls (82,365 → 154,452), which *looks like* a truncated
   first emit being fed back into the retry prompt (compounding). Confirm or refute.

Both are resolved below. Neither anomaly is a second hidden limit or an emit-re-feed bug.

---

## 1. The run, reconstructed

**Evidence base `[V]`.** Started `20:26:12Z`, ended `20:36:33Z` (~10m21s), `status: failed`,
`num_llm_calls: 2`, `total_cost_usd: 2.932835`. `cache_read=0`/`cache_write=0` on **both**
entries (caching unwired). `lineage.model: "claude-sonnet"` in `run.json` — **wrong** (see §7);
`cost.yaml` correctly records the billed `claude-opus-4-8`.

| | Call 1 (`extract_node` #1) | Call 2 (`extract_node` #2, structural retry) |
|---|---|---|
| input tokens | 82,365 | 154,452 |
| output tokens | 31,701 | 38,249 |
| outcome | success | **failed (truncated)** |
| cost | $1.20435 | $1.728485 |

**Checkpoint trajectory `[V]`** (decoded `checkpoint.sqlite`): ingest → **extract#1**
(`spec` with `chain_steps=8`, `reprompts=0`, **0 proposals, 0 lookups**; `structural_attempts=1`;
branch → `validate_static_schema`) → **validate** (13 findings: **9 `unknown_facet`** +
**4 `unknown_thesis_type`**; `route='extract'`) → **extract#2** → final checkpoint
`__error__ = EmitTruncated(...)`.

**Pricing cross-check `[V]`** ($5/Mtok input, $25/Mtok output, the confirmed `claude-opus-4-8`
rates): 82,365×5 + 31,701×25 = **$1.20435** (exact); 154,452×5 + 38,249×25 = **$1.7285**
(match). So the token figures are genuine billed aggregates, not a reporting artifact.

---

## 2. Anomaly #1 — the "two limits" question: there is one ceiling, and 16384 is correct

**16,384 is the single real PER-REQUEST output ceiling `[V]`.**
`DEFAULT_EXTRACTOR_MAX_TOKENS = 16384` (`extractor.py:90`) → `max_tokens=self._max_output_tokens`
(`extractor.py:184`) → `model_settings={"max_tokens": …}` (`anthropic_provider.py:215`), which
Anthropic applies **per model-request**. The halt message interpolates that same value
(`anthropic_provider.py:259`). No stale number, no second limit.

**The 31,701 / 38,249 figures are per-`_invoke` `RunUsage` AGGREGATES `[V]`.** `_invoke` reads
`run.usage` — pydantic-ai's `RunUsage`, which **sums** `input_tokens`/`output_tokens` across
*every* model-request inside one `agent.iter` (tool-loop turns + output-retries) — and
`_run_usage_to_token_usage` passes the cumulative `output_tokens` straight through
(`anthropic_provider.py:588-597`). `cost.yaml` records that per-`_invoke` aggregate. So one
request can't exceed 16,384; several summed can. extract#1's 31,701 over **0 tool calls**
implies **≥2 emit requests** (the output-retry budget, `retries={"output":2}`); extended
thinking is off and caching is unwired, so those are real emit tokens. **No contradiction.**

**The only genuine (minor) defect here is observability `[V → fixed as item C]`:** nothing
persisted the *per-request* breakdown, so an operator reading `cost.yaml` alone cannot see that
the *final* emit hit 16,384 while the call aggregated to 38,249. The number is right; the
decomposition was missing. (`RunUsage` carries a `.requests` count and the aggregate, so the
achievable decomposition is "limit L per request, aggregate A over K requests" — the
per-request split lives only in the Phoenix trace.)

---

## 3. Anomaly #2 — input doubling: the re-feed hypothesis is REFUTED

**Refuted as stated `[V]`.** The truncated emit is *not* fed back. (a) extract#1 was **not**
truncated — it succeeded with a complete spec. (b) The structural-retry path feeds back **only
the findings text**: `summary = f"{summary}\n\n{pending.render()}"` then
`extract(blog_content=…, source_summary=summary)` (`orchestrator.py:464-469`). The prior spec
YAML is re-embedded **only** on the jury-`revise` `refine` path (`extractor.py:355-356`), which
**did not run** (0 jury calls; `route='extract'`, a STRUCTURAL_RETRY).

**Real cause of the doubling `[V mechanism / I exact per-request counts]`.** extract#2 is a full
**re-extraction** (`validation.md §6.10`; never a patch) that must *also* emit ~13 proposal
tool-calls for the flagged terms — roughly **double the model-requests** of extract#1's
zero-tool run. With caching unwired, **every** request re-bills the **~41K base — dominated by
the schema-heavy extractor SYSTEM prompt, not the blog** (the cached normalized blog is only
4,158 words / ~6–8K tokens). Aggregate input ≈ requests × base, so ~2× requests ≈ ~2× input. A
real compounding dynamic exists (a structural retry is strictly costlier *and* is where
truncation strikes), but it is **bounded** (structural cap 3; truncation halts on first
occurrence) and is **not** an emit-re-feed bug.

---

## 4. Spec size and the calibration finding

The persisted `spec.yaml` is **complete** (extract#1's output): **8 chain_steps, 56,467 bytes /
1,379 lines, ~16K output tokens** `[V]`. Granularity is **reasonable, not over-decomposition** —
8 genuinely distinct ATT&CK phases (credential-theft → recon → privesc → lateral-movement →
collection → llmjacking → GPU-provisioning → defense-evasion). The ~7KB/step is driven by the
**mandatory per-leaf provenance** (`value/source/citations/blog_excerpt`) plus
`real_world_incidents` + `defender_techniques` + 8 `defenses` — the schema contract, not
redundant steps.

**The 16,384 ceiling is under-provisioned for the dense tail `[V]`.** ADR 0032 calibrated 16384
against "a measured 9-step Sysdig spec [that] serialises to ~12K output tokens" (`0032`); this
8-step spec is **~16K — ~33% larger than the calibration sample, sitting exactly at the
ceiling**. Dense multi-phase incident write-ups will routinely approach or exceed it. This blog
is **at the boundary, not pathological**.

---

## 5. The atomic emit, and why the ceiling cannot be meaningfully raised

**The emit is genuinely atomic `[V]`.** `output_type=AttackSpec` forces the whole spec as one
tool-call's args (`anthropic_provider.py:209-211`); `finish_reason='length'` → `EmitTruncated`,
nothing salvageable (`anthropic_provider.py:252-263`). Same forced-emit atomicity ADR 0054 noted
for the A1 patch. `EmitTruncated` is non-retryable and re-raised past the structural budget
(`call_surface.py:197-205`), so the run fails on first truncation regardless of remaining caps.

**Raising the ceiling buys little `[V]`.** The Anthropic SDK refuses a **non-streaming** request
whose estimated time exceeds 10 minutes, i.e. `max_tokens` above
`600/3600 × 128000 ≈ 21,333` raises `ValueError`
(`anthropic._base_client._calculate_nonstreaming_timeout`; `extractor.py:80-84`). The current
spec ~16K → headroom to ~21K is only **~30% / ~2 chain steps**. A bump helps *this* class of
blog but does **not** scale (`chain_steps` has no schema maximum).

**The durable class-fix is streaming + a sectioned/continuation emit (deferred).** Streaming
removes the 10-minute non-streaming wall (the per-request ceiling rises toward the 128K model
max); a sectioned emit (skeleton, then chain-steps in bounded batches, assembled and
whole-validated framework-side) handles specs beyond even that. Both touch the locked provider
call surface and need their own design ADR + gate. This is the deferred **D1/D2 / P4** work
(`0032`, `0033`, `implementation-plan.md §4.6`) — out of scope for the NOW batch.

---

## 6. Root cause — reactive proposals make every novel-vocabulary blog pay a second emit

The deepest finding `[V]`: the Extractor emits registry proposals **reactively**. extract#1
produced **0 proposals despite 13 unknown-vocabulary terms** (the CodeBuild run behaved
identically — 0 proposals on its first extract). So a structural validation rejection is *forced*
for any blog whose vocabulary isn't already registered, which routes back to a **full
re-extraction** (extract#2) — and **that second emit is the truncation-prone one** (it is larger
*and* must additionally emit the proposals).

The decisive observation: **extract#1's ~16K spec FIT** (it succeeded, complete). Had extract#1
proposed the unknown facets/thesis-types **on the first pass**, provisional resolution (ADR 0044)
would have cleared the 13 findings on pass 1 → **no structural retry** → the fitting spec
**ships**. The truncation is therefore partly downstream of "the first pass didn't propose."

This is item **B-ii**: steer the Extractor (prompt) to propose unknown vocabulary up front. It is
*not* a guaranteed truncation fix (a single dense spec can still exceed the ceiling), but it
removes the systematic doubling — fewer structural retries means far fewer second-emit
truncations and roughly halves novel-blog cost. The behavioral payoff is confirmable only in a
live run. **Overlap note:** the proposal lifecycle / in-loop steering is the held **E1 /
consolidation** work (ADR 0050); B-ii is kept **prompt-only** so it does not pre-empt that
framework rework.

### Interactions with the recent hardening `[V]`

- **No-progress bail (ADR 0057):** never fired — it needs the *same* findings on consecutive
  validate passes; extract#2 truncated *before* a second validate.
- **Global cap (ADR 0056) / structural cap:** not hit — `total_iterations=2`,
  `structural_attempts=1`; every budget had room. The run died on truncation, under all caps.
  That is "why 2 calls".
- **Persistence (L4/G1):** worked — the complete extract#1 spec was saved despite the failure.
- **Category fixes (ADR 0058):** orthogonal and working — the run got *past* content checks
  (`reprompts=0`); the proposal cap (F1) was never approached (0 first-pass proposals).

---

## 7. The model-attribution defect (same class as the run-store provenance rule)

`run.json` recorded `lineage.model: "claude-sonnet"` while `cost.yaml` correctly billed
`claude-opus-4-8` `[V]`. Cause: `_persist_from_state` set lineage from the **LLM-authored content
field** — `handle.update_lineage(model=str(state.spec.extraction_metadata.model), …)`
(`cli/extract.py:942-943`). The model wrote `"claude-sonnet"` into its own
`extraction_metadata.model`; that overrode the real billed model because the ledger fallback
(`_populate_lineage:928-930`) only fired when `lineage.model is None`, and the spec path had
already populated it. This is an `architecture.md §1.5` violation (LLM content used as framework
provenance). Fixed as item **E**: source `lineage.model` from the billed provider ledger
(authoritative), never from `extraction_metadata.model`.

**Sibling instance flagged, not fixed here `[V]`:** `cli/extract.py:756` sets
`proposed_by_model=str(result.spec.extraction_metadata.model)` in the proposal audit block — the
same LLM-self-report-as-provenance shape. It lives in a different subsystem (proposal audit) and
was out of this batch's scope; recorded here for a separate decision.

---

## 8. Doc / ADR check — behavior is consistent, one stale assumption

Behavior is **consistent with documented design** — ADR 0032/0033 predicted this precisely: "a
sufficiently long blog produces an AttackSpec that exceeds *any* fixed `max_tokens` and truncates
with no recourse … the real fix is streaming + chunked/continuation emit, deliberately out of
scope" (`0033`, `0032`). The deferred risk materialised. The **one stale assumption** is ADR
0032's calibration note ("16384 covers a realistically rich spec with margin; a 9-step spec
~12K"), falsified at the dense end by this ~16K/8-step spec — corrected in item A-NOW.

---

## The fixes (the NOW batch implemented alongside this capture)

| Item | What | Stopgap / durable | Touches |
|---|---|---|---|
| **A-NOW** | Raise `DEFAULT_EXTRACTOR_MAX_TOKENS` 16,384 → 20,000 (below the ~21,333 SDK wall); correct the ADR-0032 calibration note. | **Stopgap.** Does *not* close the truncation class — specs >~20K still truncate; streaming is the durable fix (§5). | a constant + docstring; no locked surface |
| **E** | Source `lineage.model` from the billed ledger, never `extraction_metadata.model` (§7). | durable (correctness) | `cli/extract.py`; no contract |
| **C** | Make the `EmitTruncated` message state the per-request limit vs the aggregate-over-K-requests, so the units are not misread (§2). | durable (observability) | provider error message; additive, no contract |
| **B-i** | Wire Anthropic prompt caching on the static prefix (system/schema + tool definitions; message prefix) so repeated requests within a run read from cache (§3). | optimisation | **locked provider surface** → ADR 0059 |
| **B-ii** | Prompt-steer the Extractor to propose unknown vocabulary on the **first** pass (§6). | optimisation (root-cause lever); behavioral payoff pending a live run | `prompt.md`; prompt-only, deferring to E1 |

**Honesty markers.** A-NOW is explicitly a stopgap, not the class-fix. B-i's cost path was
already correct (`compute_cost` prices `cache_read`/`cache_write`; `_run_usage_to_token_usage`
copies them) — only the cache markers were missing. B-ii's truncation benefit is *probabilistic*
(fewer second emits), not a guarantee; its real-world effect needs the user's live re-run.

**Deferred (durable class-fix, NOT in this batch):** streaming the Extractor provider call +
sectioned/continuation emit (D1/D2/P4). See §5.

Cross-references: ADR 0032 (truncation max_tokens calibration), ADR 0033 (truncation halt +
billed-on-raise), ADR 0044 (provisional resolution / proposal loop), ADR 0050 (E1 proposal
lifecycle — the held work B-ii defers to), ADR 0055/0058 (the prior category-error batch),
ADR 0059 (this batch's prompt-caching change).
