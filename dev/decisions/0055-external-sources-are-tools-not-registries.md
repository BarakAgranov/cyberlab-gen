# 0055 — External sources are tool adapters, not registries; unverifiable ≠ invalid

**Date:** 2026-06-08
**Phase:** 1 (principle settled from investigation; implementation pending and sign-off-gated)
**Architecture refs:** `schema.md §4.14` (external_data_sources vs static_catalogs — the
tool-catalog framing, the keeper), `architecture.md §1.5`/`§1.6` (LLM/framework split;
mechanical checks), `validation.md §6.4`/`§6.10` (static-schema findings → structural retry),
ADR 0042 (external lookup against an unavailable source is never fatal).
**Amends:** ADR 0044 (the "maintainer-PR-only" framing of external_data_sources — true clause,
wrong reason), ADR 0050 (the proposal-authority-by-registry list that includes
external_data_sources), and the MITRE-as-closed-catalog grouping that leans on ADR 0016.
**Evidence:** `dev/investigations/0001-external-sources-and-convergence.md`.

## Context

An `--auto extract` on the Wiz CodeBuild blog spent $7.40 over 6 LLM calls and never shipped.
The investigation (findings doc 0001) found two category errors, both of the same family —
*the framework demands a value be present in a local thing, and hard-fails correct extraction
when it isn't, instead of degrading*:

1. `_check_mitre` (`extractor.py:422`) rejects real, current ATT&CK technique ids (e.g. the
   blog-central T1195 Supply Chain Compromise, T1199 Trusted Relationship) as "hallucinations"
   because they are absent from an 8-entry bundled seed catalog whose own header says it is "a
   seed subset… NOT a live mirror." This drove ~⅔ of the spend.
2. `AdvisoryReference.source` — a publisher provenance label (`source: aws`) — is validated by
   `_check_external_sources` (`static_schema_validator.py:287-317`) against the
   `external_data_sources` registry, which holds only the one wired tool, `nvd`. A correct AWS
   advisory can never resolve; it is the lone unconvergeable finding that blocked shipping.

The root confusion is conflating two unrelated things that the code's own schema already keeps
apart: **tool adapters** (`external_data_sources` and `static_catalogs`, which share the
queryable-HTTP-adapter base `_ExternalSourceEntryBase` in `registries.py:119`) versus
**controlled vocabularies** (`facets`, `thesis_types`, `value_types`, `execution_contexts`,
which the agent proposes and which make the schema dynamic). MITRE is external-authority data
mis-built as a local catalog; `advisory.source` is a content label mis-typed as a tool id.

## Decision

Two principles govern all future work on external sources and grounding checks.

**P1 — `external_data_sources` are TOOL ADAPTERS for verification/enrichment, not controlled
vocabularies and not proposable registries.** They are authoritative systems the framework
*queries at runtime* (via the `external_lookup` tool and during enrichment), each implemented as
an adapter (a code module under `cyberlab_gen/external_data_sources/<id>/`, with operations,
cache policy, and an endpoint contract — `schema.md §4.14`, `registries.py`). The agent **never
proposes** an external source. Its only decision is: *for a value I extracted, do I need to
verify or enrich it against an external tool?* — if the blog states it, cite it; if it is needed
and an adapter is wired, call the tool to fetch the authoritative value; if no adapter is wired,
degrade gracefully. This is categorically distinct from the vocabulary registries (`facets`,
`thesis_types`, `value_types`, `execution_contexts`) that the agent **does** propose and that
evolve via the propose → provisional-resolution → promote-on-ship lifecycle (ADR 0044/0045/0050).

**P2 — an unverifiable-but-well-formed identifier must never hard-fail.** A correctly-formatted
identifier (a valid-pattern MITRE technique id, a valid CVE id, …) that cannot be verified
because the authoritative source/adapter is unavailable, unwired, or absent from a local seed
must pass as **unverified / flagged** (`requires external research`) — never be rejected as a
structural failure or a "hallucination." The reference implementation already in the codebase is
**`nvd` + `_check_cves`**: there is no local CVE list, and `_check_cves` skips gracefully when
`nvd_client is None` (`extractor.py:450`), while `external_lookup` degrades when a source is
unavailable (`tools.py:234-272`, ADR 0042). Every grounding check is held to this shape:
*verify when you can, flag when you can't, reject only true malformation.*

A "closed local catalog" remains legitimate **only** for project-owned controlled vocabularies
(the severity/detection/provisioning enums checked for drift by
`_check_closed_catalog_membership`). It is illegitimate for external-authority data (MITRE
techniques, CVEs, packages, repos), which belong behind a tool adapter.

## Alternatives considered

- **Make the model satisfy the rules (remap/drop the `aws` advisory; delete uncatalogued
  techniques).** Rejected — and explicitly **withdrawn** from the prior session. It degrades
  correct extraction to appease a category-confused mechanism; the run shows the model already
  doing this (`aws → vendor_site`) and still failing. The fix belongs in the schema/validator,
  not in model behavior.
- **Grow the bundled MITRE catalog.** Rejected as the *mechanism*: a larger seed is still a
  local membership gate that will reject the next out-of-seed technique. The bug is the gate, not
  the catalog's size.

## Consequences

- **Implementation is pending and sign-off-gated**, because it touches the validation contract
  (`validation.md §6.4`) and prompt/schema surfaces. The cheap, ship-unblocking NOW changes
  (ungate `_check_mitre` to the `_check_cves` shape; drop `advisory.source` from
  `_check_external_sources`; validate `cve.source_of_record` only post-enrichment; fix the
  prompt's GitHub/packages over-promise) and the real LATER adapter builds (NVD client; a MITRE
  ATT&CK adapter with `lookup_by_id` + `lookup_by_description`; OSV) are enumerated with
  file:line in findings doc 0001 §5. No code or contract is changed by recording this ADR.
- **Doc/ADR cleanup is owed** (findings doc 0001 §6): `schema.md §4.16` and `validation.md §6.4`
  stop listing `external_data_sources` as a proposable vocabulary registry (keeper:
  `schema.md §4.14`); the MITRE doc split (`agents.md:79` tool-framing vs `pipeline.md:78`
  local-framing) is reconciled toward the tool model; `loader.py`'s MITRE-with-closed-catalogs
  grouping is revisited. These are architect/maintainer doc edits, not implementation-task edits.
- **The wider hunt's negative result stands:** the conflation narrows to one true mis-build
  (MITRE) plus one adjacent content-field case (`advisory.source`). The look-alikes
  (closed-enum drift guard; GitHub/packages; `static_catalogs`) are not mis-builds. The fix
  surface is small and bounded.
- No change to `architecture.md §1.5`/`§1.6` — this upholds them (the gate stays mechanical; the
  agent still only produces content and decides to call a tool). It sharpens what "resolve into a
  registry" may and may not mean.
