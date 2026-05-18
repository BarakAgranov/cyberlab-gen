# 0007 — `path_template` is `str`, not `NonEmptyString`

**Date:** 2026-05-18
**Phase:** Phase 0 (post-Task 3)
**Architecture refs:** `docs/schema-details.md §6.3`, `docs/registry-details.md §4.2`, `docs/registry-details.md §5.2`

## Decision

`ExternalSourceEndpoint.path_template` is typed as `str`, not `NonEmptyString`. Empty strings are valid for endpoints where the full URL is encoded in `base_url` and no path suffix is needed.

## Context

`schema-details.md §6.3` originally typed `path_template: NonEmptyString`, intending to catch typos where someone forgot to fill in the template. But `registry-details.md` ships seed entries where `path_template: ""` is the correct, intentional value:

- RSS feeds: AWS Security Bulletins, Azure Security Advisories, GCP Security Bulletins. The base_url is the full feed URL.
- Static catalogs: AWS IAM, Azure RBAC, GCP IAM. The base_url is the full asset URL.

7 of 13 v1 seed entries use `path_template: ""`. The schema and the seeds contradict each other.

Discovered during Task 3 plan execution. The test for `nvd` (which uses a real non-empty `path_template`) passes; loading the real seeds in Task 4 would fail Layer 1 validation if the constraint stays.

## Alternatives considered

- **Keep `NonEmptyString`, change the seeds to `"/"` or other non-empty placeholder.** Rejected: less honest — the placeholder carries a value that doesn't match reality. Provides false signal to readers of the registry.
- **Use `Literal[""] | NonEmptyString`.** Rejected: complexity for small win. The discriminator's job (catch typos) is better handled by Layer 3 runtime validation (the framework fetches the URL and fails if it's malformed).
- **Relax to `str`** (chosen). Honest to the registry's actual content. Typo-catching responsibility moves to Layer 3.

## Consequences

- `cyberlab_gen/schemas/registries.py`: `path_template: str` instead of `NonEmptyString`.
- `docs/schema-details.md §6.3`: same change in the doc's class definition.
- Task 4's registry loader can ship the documented seeds without rewriting them.
- Tests do not need updating; the existing `nvd` happy path uses non-empty templates and the static-catalog happy path can be updated to use empty `path_template` (matching real seeds).

## Supersedes

This ADR supersedes the prior 0007 (dated 2026-05-18, Phase 0 Task 3), which had deferred the decision and kept `NonEmptyString`. The deferral pointed at the architect to choose between relaxing the schema or rewriting the seeds; the architect chose relaxation, recorded here.
