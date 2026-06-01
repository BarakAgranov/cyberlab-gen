# 0021 — Extractor/Jury output envelopes, in-flight proposals, and the framework rejection-and-reprompt loop

**Date:** 2026-06-01
**Phase:** Phase 1 (Task 5)
**Architecture refs:** `agents.md §5.4`, `§5.5`, `pipeline.md §3.2.2`, `§3.2.3`, `schema.md §4.15`, `§4.16`, `§4.20`, `§4.10`, `provider-interface.md §4`, `architecture.md §1.5`, ADR 0018

## Decision

1. **The Extractor's *typed agent output* is `AttackSpec`** (the brief's "Pydantic AI agent with `AttackSpec` output type"). Proposals (`propose_value_type` / `propose_facet`) and the external-lookup trace are **not** part of `AttackSpec`; they are emitted via tool calls and collected by the framework-side `ToolExecutor`. The Extractor *stage* returns an internal `ExtractionResult` envelope wrapping the validated `AttackSpec`, the collected proposals, the external-lookup trace, and the stage outcome (accepted / rejected-and-reprompted). `ExtractionResult` is an `InternalModel` — it never crosses to disk as an artifact; only the `AttackSpec` inside it does.

2. **In-flight proposals are typed as `ProposedValueType` / `ProposedFacet`** (`cyberlab_gen/agents/proposals.py`, `InternalModel`). These are the agent-emitted proposal *before* the framework writes the overlay. They are distinct from the overlay-resident `ProposalAuditBlock` (`schemas/registries.py`) — the audit block is framework-recorded acceptance history (`proposed_by_model`, `proposed_at`, …) written when a proposal is accepted into the overlay (`schema.md §4.16`, "framework-recorded, not agent-authored"). The agent authors only the *content* of the proposed entry plus its reasoning; the framework stamps the audit metadata at accept time (Task 7's interrupt / `--auto` accept). The split mirrors the doc's "entries and proposals are separate" rule.

3. **`propose_facet` rejects non-`target:*` / non-blog-derived-`lab_class_signal:*` categories at the tool boundary.** The Extractor is *not* the authority for `runtime:*` or lab-derived `lab_class_signal:*` facets (`schema.md §4.16`, `agents.md §5.4`). A `runtime:*` proposal returns a `ToolResult(is_error=True)` telling the agent that category is the Planner's; this is mechanical, not LLM discretion.

4. **Search-before-claim and MITRE/CVE hallucination are framework-level rejections that consume the *retry* budget, never refinement** (`pipeline.md §3.2.2`, `schema.md §4.15`, `architecture.md §1.7`). After the provider returns a structurally valid `AttackSpec`, the Extractor stage runs three mechanical checks:
   - **search-before-claim:** every `source: external_api` field must have a matching `external_lookup` tool call in the trace (matched by source id + the referenced identifier). A missing match rejects the spec.
   - **MITRE hallucination:** every chain-step / external-ref technique id must resolve in the bundled MITRE catalog (`load_mitre_techniques`).
   - **CVE hallucination:** every CVE id whose provenance claims `blog_explicit`/`llm_inference`/`external_api` must resolve against NVD (via the injected `NvdClient`; when no client is wired, CVE-resolution is *skipped*, not failed — the conservative "we couldn't check" posture, recorded so it is honest, mirroring Task 4's not-integrated skips).
   On any rejection the stage re-prompts the agent with the specific offending ids/fields appended to the user turn, and decrements a `hallucination_retry_attempts` budget (placeholder 2, `architecture.md §8.4`). Budget exhaustion raises `ExtractionError` (added to `errors.py`). This is a **second, distinct** retry loop from the call surface's structural-malformation loop (ADR 0018): structural malformation = the model couldn't produce schema-valid JSON; hallucination/search-before-claim = the JSON is valid but factually ungrounded. Both are retry, neither is refinement.

5. **The Jury's typed output is `JuryVerdict`** `{verdict, scores, feedback, retry_recommended}` (`cyberlab_gen/agents/extractor_jury/schema.py`, `ArtifactModel` because it is surfaced in the run report and may round-trip). `verdict ∈ {approve, revise, reject}`; `scores` is a `JuryScores` with the four rubric dimensions (fidelity, completeness, provenance_correctness, structural_validity), each `0–1`; `feedback` is a list of `JuryFieldFeedback` (each names a `field_path` + problem); `retry_recommended: bool`. The framework — not the jury — maps a verdict to control flow (`agents.md §5.5`). The verdict-vs-score consistency rule (`revise` ⇒ 1–3 feedback items; `reject` ⇒ a feedback item count or a dimension floor breach consistent with ">30 % of content fields mismatched") is enforced by a `model_validator`, so a malformed verdict fails structurally rather than silently mis-routing.

6. **Provenance-mismatch verification is a framework helper** (`verify_provenance`) the Jury *and* the orchestrator can call, returning per-field `ProvenanceFinding`s by source kind: `blog_explicit` ⇒ a `blog_passage` citation must be present; `external_api` ⇒ an `external_api_response` citation must be present *and* a matching tool call must exist in the trace (the jury independently re-runs search-before-claim, `agents.md §5.5`); `llm_inference` ⇒ confidence + at least one citation present; `unknown_from_blog` ⇒ a reason present and no citations. This is mechanical structure-checking that grounds the jury's LLM judgment; the *semantic* "does the passage actually say this" check remains the LLM's job inside the jury prompt.

## Context

`agents.md §5.4/§5.5` and `pipeline.md §3.2.2/§3.2.3` specify the Extractor and Jury contracts richly but leave three things to implementation:

- **The wire shape of an in-flight proposal.** The docs pin the *overlay file* shape (`ProposalAuditBlock`, ADR 0015/0016) and the *registry entry* shape (`ValueTypeEntry`, `FacetEntry`), but not the object the agent emits through `propose_value_type` before any overlay write. We need a typed object so "no free text across stage boundaries" holds.
- **Where proposals and the tool trace live relative to the `AttackSpec` output.** Making them `AttackSpec` fields would (a) contradict "AttackSpec output type" and (b) leak run-mechanics (tool traces) into a user-editable artifact. An internal envelope is the clean separation.
- **The mechanism of "rejects the output and re-prompts … counts against the stage's retry budget"** (`pipeline.md §3.2.2`). The call surface (Task 2/ADR 0018) only retries *structural* malformation. Search-before-claim and hallucination rejection are a separate, content-level retry the Extractor stage owns.

## Alternatives considered

- **Put proposals + trace on the `AttackSpec`.** Rejected: contradicts the declared output type and pollutes the artifact with run mechanics.
- **Reuse `ProposalAuditBlock` as the in-flight proposal.** Rejected: that block holds framework-recorded audit metadata (`proposed_by_model`, `proposed_at`) that the agent must *not* author (`schema.md §4.16`). The agent authors content + reasoning only.
- **Fold hallucination rejection into the call surface's structural-retry loop.** Rejected: the call surface is generic across all agents and only knows about schema-parse failures; hallucination checks need the AttackSpec semantics, the MITRE catalog, and the tool trace. Keeping them in the Extractor stage keeps the call surface generic and the two retry budgets independently calibratable (`architecture.md §8.4`).
- **Let the jury route control flow (decide retry).** Rejected: `architecture.md §1.5` — LLMs never route control flow. The jury emits a judgment + `retry_recommended` advisory; the framework decides.

## Doc-improvement note for the next brief writer

`agents.md §5.4` lists a `propose_external_source_pattern` tool for the Extractor ("surfaced for maintainer PR review, not auto-added"). The Task 5 brief's tool list (item 2) omits it. Task 5 implements the three tools the brief names (`external_lookup`, `propose_value_type`, `propose_facet`); `propose_external_source_pattern` is deferred as out-of-scope for the brief and flagged here. Phase 2's brief should reconcile the tool inventory between `agents.md §5.4` and the task decomposition.
