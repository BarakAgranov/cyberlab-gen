# ADR 0014: Blog-set manifest schema

**Status:** Accepted (Phase 0 Task 8)
**Date:** 2026-05-18
**Decider:** Task 8 implementation agent

## Context

`docs/eval.md §7.3` (line 59) references `eval/blog-sets/manifest.yaml` —
*"The exact list lives in `eval/blog-sets/manifest.yaml`, versioned with the
repo"* — but specifies no schema for the file. The architecture documents
elsewhere (`schema.md`) describe AttackSpec and LabManifest envelopes but say
nothing about how the blog set is enumerated. The eval harness that will
consume the manifest lands in Phase 4 (`implementation-plan.md §7`), so the
loader is not in Phase 0 either.

`CLAUDE.md` rules: *"Never resolve architectural ambiguities silently. If you
read the docs and something is unclear or under-specified, record the question
in `dev/decisions/NNNN-<slug>.md`."* Task 8 needs the manifest skeleton to land
now (so the three placeholder entries point somewhere); the shape needs to be
invented and recorded.

The forward-compat constraints come from `eval.md §7.3`:

1. **Curated and held-out as distinct sets.** §7.3 lines 40–42 split the eval
   corpus into a `curated` set (visible during development) and a `held_out`
   set (reserved for generalization measurement). The split is structural, not
   tag-based — a blog is in exactly one set at a time. Phase 0 has no held-out
   entries (rotation lands in Phase 4) but the split must be present from day 1
   so v0.2+ can add held-out entries without a schema change.
2. **Rotation generation tracking.** §7.3 lines 65–68 describe per-release
   rotation between the two sets. Eval runs *"record which rotation generation
   they used"* (line 70). The manifest is the natural place to record the
   current generation.
3. **Coverage tagging.** §7.3 lines 44–57 list 8+ coverage dimensions (clouds,
   complexity tiers, thesis types, lab_class_signal facets). Line 57: *"The
   harness emits a coverage matrix per release showing which requirements each
   blog satisfies."* The manifest needs to carry the tags the matrix is built
   from; without them, the matrix becomes a separate side-channel artifact.
4. **No remote registry fetching** (`schema.md §4.11`). The manifest is
   committed to the repo; loaders consume the file on disk.

## Decision

The Phase 0 manifest shape:

```yaml
spec_version: 1
spec_kind: BlogSetManifest
rotation_generation: 0

curated:
  - id: <slug>
    shape: aws_ttp | supply_chain | incident_analysis
    url: <string | TBD>
    title: <string | TBD>
    publisher: <string | TBD>
    accessed_date: <ISO 8601 date | TBD>
    walk: <relative path to dev/curated-blog-walks/<id>.md>
    coverage_tags: [<string>, ...]

held_out: []
```

**Field rationale:**

- `spec_version` / `spec_kind` — mirror the AttackSpec/LabManifest envelope
  convention (`schema.md §4.8` lines 292–293, §4.4 lines 60–61). Lets future
  loaders dispatch on `spec_kind` and version-gate via `spec_version`.
- `rotation_generation` — integer; bumped each release per `eval.md §7.3` line
  70. Phase 0 ships generation 0 (pre-release). Eval reports cite the
  generation; rotation history lives in release notes (§7.3 line 68).
- `curated` / `held_out` — separate ordered lists. Both present from day 1
  even though `held_out` is empty in Phase 0; v0.2+ populates it.
- Per-entry `id` — slug, used as the entry's stable identifier across rotation
  generations and as the slug for the walk file.
- Per-entry `shape` — one of `aws_ttp` / `supply_chain` / `incident_analysis`,
  matching the three required shapes from `implementation-plan.md §3.2` lines
  180–182. Open-set; v0.2+ may add shapes (e.g., `cross_tenant`, `entra_id`).
- Per-entry blog metadata (`url`, `title`, `publisher`, `accessed_date`) —
  duplicated from the walk's §1 header by design. The walk is the source of
  truth for *content*; the manifest is the source of truth for *what's in the
  eval set*. Cross-loading the manifest doesn't require parsing every walk.
- Per-entry `walk` — relative path from repo root to the walk file. Phase 0
  placeholders point to files that don't exist; future test (Phase 1+) can
  mechanically check the `walk:` paths resolve once real walks are written.
- Per-entry `coverage_tags` — free-form list of strings drawn from `eval.md
  §7.3`'s coverage dimensions. Phase 0 entries leave the list empty
  (placeholders aren't walked yet); Phase 4's coverage-matrix tooling reads
  the lists and reports the matrix.

**Alternatives considered:**

1. **Flat list with a `set: curated|held_out` field on each entry.** Rejected:
   structural split is clearer for the rotation-generation invariant ("an
   entry is in exactly one list per generation"). With a flat list, the
   rotation policy becomes "edit the `set:` field on existing entries,"
   which is less honest about the move than physically reseating the entry.
2. **No `spec_version` / `spec_kind` envelope; just `curated:` / `held_out:`
   as top-level keys.** Rejected: every other schema-defined artifact in
   cyberlab-gen carries the envelope. Consistency wins; the cost is two extra
   lines.
3. **Coverage tags as a separate file (`eval/blog-sets/coverage-matrix.yaml`).**
   Rejected for Phase 0: the cross-reference burden (matrix entries refer to
   manifest entries by id, manifest entries don't know what tags they have)
   is worse than co-location. Phase 4 may want the separate matrix file when
   the harness emits the per-release report — that's a Phase 4 decision, not
   a Phase 0 one.
4. **Inline the walk content into the manifest.** Rejected: walks are
   markdown with narrative content; the manifest is YAML with structured
   metadata. Combining them creates a YAML-with-embedded-prose document that
   no loader and no human likes.

## Consequences

- The manifest schema is *invented*, not architecturally specified. Future
  loaders depend on this ADR rather than on `docs/`. A doc-improvement
  suggestion has been surfaced via the Task 8 execution log entry: promote
  this schema into `eval.md §7.3` (or a new `§7.3.1`).
- No Pydantic `BlogSetManifest` model in Phase 0. The shape ships as a YAML
  file the human reads and edits; a Pydantic loader lands in Phase 4 when the
  eval harness consumes the manifest.
- The Phase 0 manifest's `walk:` paths point to files that don't exist yet
  (the human writes them after picking blogs). This is intentional: the
  manifest skeleton lands first, the three real walks land second.
- The `coverage_tags` field is open-set. Phase 4's coverage-matrix tooling
  needs to validate the tags against `eval.md §7.3`'s coverage dimensions;
  that validation is part of the Phase 4 brief, not Phase 0.
- Held-out set rotation is deferred to v0.2+ but the shape supports it from
  generation 0. Adding held-out entries won't be a schema change — just a
  data change.

## Doc-improvement recommended

The next Phase-0-brief sweep (or whenever the user routes doc edits) should:

1. Update Task 8's brief citations: `eval.md §7.3` (not `§2`/`§3`); add
   `schema.md §4.8` + `§4.12` + `§4.13` (not the §4.4–§4.8 range, which
   overshoots into LabManifest territory).
2. Consider promoting the schema in this ADR into `eval.md §7.3` (or a new
   `§7.3.1`) so the next implementer reading §7.3 finds the shape there
   rather than chasing this ADR.

## References

- `docs/eval.md §7.3` (lines 36–70) — blog set composition, coverage, rotation.
- `docs/schema.md §4.4` (line 53+) — envelope convention precedent.
- `docs/schema.md §4.8` (line 287+) — AttackSpec envelope; the walk's structural
  backbone.
- `docs/implementation-plan.md §3.2` (lines 178–184) — the three required blog
  shapes (`aws_ttp`, `supply_chain`, `incident_analysis`).
- `dev/phase-briefs/phase-0-agent-brief.md` lines 408–442 — Task 8 brief.
- `eval/blog-sets/manifest.yaml` — the Phase 0 instance of this schema.
- `dev/curated-blog-walks/template.md` — the walk template that the
  manifest's `walk:` paths point at.
- `CLAUDE.md` — "Never resolve architectural ambiguities silently."
