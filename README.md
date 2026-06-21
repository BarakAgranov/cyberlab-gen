# cyberlab-gen


cyberlab-gen is a command-line tool that turns a published security writeup — a
blog post, research report, or advisory at a URL — into a runnable hands-on cyber lab: 
infrastructure-as-code, attack scripts, detection rules, setup/teardown scripts, 
documentation, and a typed manifest that records, step by step,
*how faithfully* the lab reproduces the original.

You bring your own LLM API keys and your own cloud account. You point the tool
at a URL and get a lab directory you run on infrastructure you control — and
tear down cleanly when you're done.


## How it works

A writeup describes an attack in prose. Turning that into a lab that is
*faithful*, *safe*, and *actually runnable* is the hard part, and it is where
most of the engineering lives. cyberlab-gen treats it as a pipeline of
specialized stages, each producing a typed, provenance-tracked artifact the next
stage consumes:

```
  writeup URL
      │
  Extract  ──►  AttackSpec   the attack as structured data: thesis, chain of
      │                      steps, techniques & CVEs, defenses, and a per-step
      │                      reproducibility judgment — every field provenance-tagged
  Plan     ──►  LabManifest  the lab's skeleton (lab.yaml): which steps become
      │                      phases, which become provisioned resources, and the
      │                      typed values that flow between them
  Generate ──►  lab/         the manifest materialized into IaC, attack scripts,
      │                      detections, and lifecycle scripts
  Validate ──►  findings     mechanical structural checks for anything
   & Critique                deterministic; an LLM Critic only for the judgment-heavy parts
      │
  Refine        route each finding back to the stage that owns it and re-run —
                fix the cause, not the downstream symptom
```

Every arrow is a typed boundary — no free text passes between stages. Two
contracts carry the work across the pipeline: the **AttackSpec** (what the
writeup says) and the **LabManifest** (what the lab will be). The manifest is
the single source of truth for the lab's structure, so what a lab *claims* to be
and what it actually *contains* cannot quietly drift apart.


## Two principles

**1 — The orchestrator is deterministic; the models only produce content and
judgments.** A state machine drives the pipeline. LLMs *do* two things: produce
content (extraction, planning, generation, docs) and produce structured
judgments (jury verdicts, Critic verdicts, refinement recommendations). LLMs
*never* route control flow, decide their own retry budgets, decide when to stop
refining, or decide whether their output ships — those are framework code,
deterministic and auditable. Mechanizable safety-critical checks (credential-
pattern scanning, the cleanup-confidence gate) are mechanical, never an LLM's
opinion. This keeps the pipeline's failure modes legible.

**2 — Honesty over completeness.** Every field records where it came from —
quoted from the source, inferred by the model (with a confidence score),
retrieved from an external lookup, or supplied by you. Reproducibility is graded
*per step* — `full`, `partial_simulation`, `demonstration_only`, or
`not_reproducible` — and a step that can't be faithfully reproduced is simulated
and labeled, documented and labeled, or dropped — but never silently faked.
Cost, failure, and uncertainty are reported plainly.

## Usage

The whole tool is built around one command:

```
cyberlab-gen generate <url>
```

It runs the full pipeline and writes a lab directory you can review, deploy, and
tear down. Companion verbs inspect and repair what was generated:

```
cyberlab-gen validate <lab-dir>        # check a generated lab against its manifest
cyberlab-gen fix <lab-dir>             # route validation findings back and regenerate
cyberlab-gen telemetry submit          # submit queued, sanitized run telemetry (opt-in)
```

See [Status](#status) for which of these run today.

## Scope & safety

cyberlab-gen is for **defensive and educational use** — building purple-team
environments to understand attacks well enough to detect and defend against
them, against targets you provision yourself (cloud and platform accounts such
as AWS, Azure, GCP, and GitHub). The safety posture is an architectural
property, not a paragraph of fine print:

- **It generates; you run.** There is deliberately no "agent that runs the lab
  for you." An AI silently executing attack chains across cloud accounts *is*
  the threat defenders exist to catch, so building one would be building the
  thing the field is trying to stop.
- **It reproduces *already-public* research,** on infrastructure *you* own and
  control. It runs locally, generates locally, validates locally — no hosted
  service, no shared state, no background telemetry.
- **Faithfulness is bounded, with honest caveats.** Where the original attack
  relied on live external infrastructure or a now-patched flaw, the lab says so
  (`partial_simulation` / `demonstration_only`) rather than pretending to
  reproduce it.
- **Out-of-scope content is refused, not best-efforted.** When a writeup can't
  become a meaningful lab, the pipeline says so and stops; mechanical scans and
  scope refusal back that up.
- **Money is mechanical.** Spend is estimated before each billed stage and
  capped by a hard ceiling; a generated lab refuses to set itself up when
  cleanup confidence is below threshold — because orphaned cloud resources are
  the one way this tool can cost you real money.

## Status

cyberlab-gen is under active development, and honesty is a project value — so
here is the real state. The **front half of the pipeline runs for real today**
against a paid LLM provider; the code-generation half is being built next.

| Stage | Command | State |
|-------|---------|-------|
| Writeup → AttackSpec | `extract <url>` | **Runs end-to-end, provider-backed** — ingestion, extraction, static-schema validation, an LLM jury pass, cost recording, and an interactive review step. |
| AttackSpec → LabManifest | `plan <attack-spec>` | **Runs end-to-end, provider-backed** — produces a structurally- and semantically-validated `lab.yaml`. |
| Manifest → lab directory | `generate <url>` | Not yet implemented — the code Generators, Critic, and Repair Agent don't exist yet. |
| `validate` / `fix` / `telemetry submit` | — | Stubs; they print a not-implemented message and exit non-zero. |

`extract` and `plan` are **developer / eval commands** — each runs a single
pipeline stage in isolation so it can be built and measured, and both are
exercised by an evaluation harness that runs them against a curated set of real
security writeups with human-reviewed ground-truth "walks." They are not the
user surface; `generate <url>` is. If a command isn't marked as running above,
assume it doesn't work yet.

> The most recent git tag lags the real state by dozens of commits — trust this
> table, the `dev/` execution logs, and the architecture decision records in
> `dev/decisions/` (more than a hundred of them) over the tag until it is
> re-cut.

## Documentation

[`docs/architecture.md`](docs/architecture.md) is the architectural hub;
[`docs/index.md`](docs/index.md) is the routing table that maps a question to
the right doc section. Start there for the LLM-vs-framework split (`§1.5`), the
mechanical-safety line (`§1.6`), and the reproducibility model.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The verify gate is `just verify`
(ruff, ruff format, pyright strict, pytest); CI re-runs it on every push.

### Observability (local Phoenix tracing)

Every LLM call and pipeline stage can be viewed as an OpenTelemetry trace in a
**local** [Arize Phoenix](https://github.com/Arize-ai/phoenix) instance — model,
tokens, cost, stop reason, prompt/response, tool calls, and the
extract → validate → jury → enrich stage tree. Data stays on your machine; there
is no cloud export. Tracing is opt-in and auto-detected: it is a no-op unless a
Phoenix instance is reachable, so normal runs are unaffected.

```
uv sync --extra observability                                  # install the extra
docker run -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest   # start a local Phoenix
```

Then run any stage (or the eval) and open the traces at
<http://localhost:6006>. `CYBERLAB_GEN_TRACING` controls it: `auto` (default —
enable when Phoenix is reachable), `off` (never), `on` (force setup even before
the probe). The endpoint defaults to `http://localhost:6006`.
