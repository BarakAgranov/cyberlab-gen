# 0104 — Lift provisional status from the curated walks (human ground-truth pass complete)

**Date:** 2026-06-20
**Phase:** 2 (v0.3 exit — architect-directed hygiene after ADR 0103)
**Architecture refs:** ADR 0102 (decision 6 — real-blog walks shipped PROVISIONAL pending a human
pass; the walk-review gate in CALIBRATION.md), ADR 0103 (manifest↔walk reconciliation, which flagged
provisional-lifting as the architect's call), `eval.md §7.2` (honest framing of OSS eval — why an
agent-drafted walk is not yet independent ground truth), `implementation-plan.md §5.4` (the six
Phase-2 calibration items).

## Context

The five Phase-2 curated walks (`entra-id`, `confusedfunction`, `netlify`, `gke-fluentbit`, `lucr-3`)
shipped agent-drafted and marked **PROVISIONAL pending a human ground-truth pass** (ADR 0102 dec. 6):
an LLM reading the same source blog is the same model class being evaluated, so it is not independent
ground truth. The architect has now decided that pass is **complete** — all five were reviewed against
their source blogs (source-fidelity checks) and every correction applied (the corrections from ADR
0103's source-verification rounds and the human review). The provisional markers are therefore stale
and misleading and must be lifted.

The three Phase-1 walks (`ai-assisted-aws-intrusion`, `aws-codebuild-actor-id-regex-bypass`,
`long-multi-stage-cloud-campaign`) never carried provisional banners — they were human-walked in
Phase 1 — so "lift the 8 walks' banners" in the brief is really **five** walks. Noted, not a problem.

## Decision

1. **Provisional status is lifted across the repo.** Each of the five walk banners is replaced with a
   one-line **blessed / human-reviewed (2026-06-20)** provenance note (kept rather than deleted, so
   provenance survives). The two in-body provisional references (confusedfunction §15 "flagged
   provisional"; gke §6 "consistent with the provisional banner") are reworded. The manifest's
   Phase-2 comment, and the CALIBRATION.md walk-review gate, are updated to "human pass complete /
   walks blessed." Forward-pointer resolution notes added to ADR 0102 (dec. 6) and ADR 0103.

2. **The two gates are distinct, and only the human-pass gate is cleared.** Lifting provisional clears
   *only* the human-ground-truth gate. **The six CALIBRATION.md calibration values stay
   locked/pending** — they depend on the *separate* paid `just eval --stage plan` calibration run,
   which has not happened. Nothing in this change unlocks, fills, or finalizes any of the six values;
   the CALIBRATION.md placeholder table and its "append the real numbers when the run lands" rule are
   untouched. Every banner and the gate note make this distinction explicit so the two are never
   conflated.

## What was deliberately NOT touched (verified)

- **The six calibration values / placeholder table** (`CALIBRATION.md` rows) — still pending the paid
  run.
- **Proposal "provisional resolution" machinery** (ADR 0044/0045/0050/0062/0099; `static_schema_
  validator.py`, `orchestrator.py`, `runner.py`, the related tests/logs) — a completely different use
  of the word "provisional" (in-flight registry-proposal resolution), unrelated to walk status.
- **Append-only execution-log history** — the Task-10 entries that recorded "walks ship provisional"
  were true when written; history is not rewritten. A forward note records the lift.
- No test asserted a banner string, so removing the banners breaks nothing (`just verify` green).

## Coverage check (ADR 0103 `:present` flattening — confirmed safe)

While in these files, confirmed the ADR-0103 tag flattening
(`vulnerability_disclosure:present`→bare, `incident_analysis:present`→bare, dropped
`defender_techniques:present`) did **not** change measured coverage: nothing in `cyberlab_gen/` or
`eval/` keys on the `:present` form (no occurrence exists), and the only required-dimension-from-tags
check (`test_curated_set_covers_the_four_required_phase2_dimensions`) already asserts the bare
`vulnerability_disclosure`, which the entra-id entry still carries. The dropped
`defender_techniques:present` was manifest-only drift; defender presence is correctly represented in
the ai-assisted and lucr-3 walks' §14 via `incident_analysis` + `lab_class_signal:expected_detections`
— no §14 gap. No fix required.
