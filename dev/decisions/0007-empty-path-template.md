# 0007 — `ExternalSourceEndpoint.path_template` conflict between schema-details and registry-details

**Date:** 2026-05-18
**Phase:** Phase 0 (Task 3)
**Architecture refs:** `docs/schema-details.md §6.3`, `docs/registry-details.md §4.2`, `docs/registry-details.md §5.2`

## Decision

Keep `ExternalSourceEndpoint.path_template: NonEmptyString` for Task 3, matching `schema-details.md §6.3` exactly. Surface the discrepancy to the architect; do not silently relax the constraint.

The seven v1 seed entries in `registry-details.md` that use `path_template: ""` (RSS-feed sources in §4.2 and all three catalogs in §5.2) cannot be loaded by Task 4 as-is. Resolution of that conflict is deferred to whichever lands first: an architect-driven doc edit, or Task 4 hitting the failure mode.

## Context

`schema-details.md §6.3` shows:

```python
class ExternalSourceEndpoint(BaseModel):
    ...
    path_template: NonEmptyString  # e.g., "/cves/{cve_id}"
```

`NonEmptyString = Annotated[str, StringConstraints(min_length=1)]` (Task 1).

`registry-details.md §5.2` shows three of the three `static_catalogs` v1 seeds with `path_template: ""`:

```yaml
- id: aws_iam_catalog
  ...
  endpoints:
    - id: catalog_download
      method: GET
      path_template: ""
```

`registry-details.md §4.2` shows the four RSS-feed `external_data_sources` v1 seeds with the same pattern (`aws_security_bulletins`, `azure_security_advisories`, `gcp_security_bulletins`, `cisa_kev`). That's 7 of 13 v1 seed entries with empty `path_template`.

The discrepancy is a docs-vs-docs conflict, not a brief-vs-doc one. Per CLAUDE.md's authority gradient, both are `docs/*.md` — equal weight. The conflict has to be resolved upstream of implementation; Task 3 cannot silently pick either reading.

The semantic intent of each seed appears to be "use the `base_url` verbatim, no path suffix." Two readings are coherent: either the schema relaxes to `path_template: str` (allowing `""`), or the seeds change to `path_template: "/"` (root path, satisfies NonEmptyString) or some other non-empty sentinel.

## Alternatives considered

- **Relax `path_template` to `str` in Task 3.** Rejected: silently makes a schema-level decision that overrides `schema-details.md §6.3`. Violates CLAUDE.md's "never resolve architectural ambiguities silently."
- **Skip the static-catalog construction test.** Rejected: removes coverage the brief explicitly calls for ("every Pydantic model parses an empty entries:list").
- **Keep `NonEmptyString`, use non-empty path_template in test fixtures, ADR the discrepancy** (chosen). Task 3 implementation strictly matches `schema-details.md §6.3`. The static-catalog test fixture uses a realistic non-empty path (e.g., `/policies.js` extracted from the base URL), with an inline comment naming this ADR. The architect can either edit `schema-details.md §6.3` to relax to `str`, or edit `registry-details.md §4.2/§5.2` to use non-empty paths, before Task 4 runs.

## Consequences

- `cyberlab_gen/schemas/registries.py` ships with `path_template: NonEmptyString` per `schema-details.md §6.3`.
- The Task 3 test fixture `_static_catalog()` uses a non-empty `path_template` so the test reflects what the schema actually accepts; an inline comment names this ADR.
- Task 4's seed-loading work will hit this discrepancy if the docs aren't resolved first. Task 4's brief points at `registry-details.md` for "the simplest entries" — those entries don't currently load. The Task 4 agent should either:
  - find the architect's doc edit has landed, and proceed; or
  - record the second-hit and stop, since Task 4 is the layer where the conflict bites.
- The execution log entry for Task 3 surfaces this prominently.
