# Eval reports archive

Archived `EvalReport`s from `just eval` runs land here, one timestamped YAML per
run (`gen<rotation-generation>-<UTC-timestamp>.yaml`). This co-location is
required by `eval.md §7.13` ("Its results live in eval reports archived in the
repo (`eval/reports/`)") and is the Task-8 exit criterion "eval reports archive
cleanly to `eval/reports/`".

Each report records whether it was **provider-backed**:

- `provider_backed: true` — a real run against a configured LLM provider
  (`ANTHROPIC_API_KEY` set). These carry real static-schema pass rates, cost, and
  completeness numbers.
- `provider_backed: false` — an **offline / fixture** run with no live model
  (the harness never fabricates model output; `eval.md §7.2` honest framing).
  The metrics in such a report come from scripted/fixture records and are a
  demonstration of the archive shape, **not** evidence about the system's
  behavior.

`gen0-20260601T120000Z.yaml` is a committed **offline demonstration** report
(`provider_backed: false`) showing the archive shape and the exit-criterion
helpers (`overall_static_schema_pass_rate`, `blogs_with_valid_spec`). The first
provider-backed run replaces the demonstration value of these numbers with real
ones; see `CALIBRATION.md` for what those numbers will calibrate.
