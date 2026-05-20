# cyberlab-gen

A command-line tool that turns a published security writeup (blog post, research
report, advisory) into a runnable, validated, hands-on cyber lab — code,
infrastructure-as-code, attack scripts, detection rules, lifecycle scripts,
documentation, and a structured manifest describing what was generated. The
user provides their own LLM API keys, points the tool at a URL, and gets a lab
directory they run on their own cloud accounts.

## Status

**Phase 0 — skeleton.** The end-to-end generation pipeline is not yet
implemented. The four CLI verbs (`generate`, `validate`, `fix`, `report`) exist
as stubs that print "not yet implemented." The repo's current phase is recorded
in the latest git tag (`v0.x.y`).

### What is in this repo right now

- Pydantic schemas (`cyberlab_gen/schemas/`) — the typed cross-stage models.
- Six registry YAML files in `registry/` with loaders and meta-schema validation.
- Provider abstraction with a mock provider, cost ledger, and model resolver.
- Local state management.
- Typer CLI scaffolding with the four stub verbs.
- An eval blog-set manifest skeleton and walk template under `eval/blog-sets/`.

### What is not yet built

The Extractor, Planner, Generator, Critic, and Refiner agents; the live
provider implementations; the validator layers; the eval harness runner. No
end-to-end generation works yet.

## Documentation

[`docs/architecture.md`](docs/architecture.md) is the architectural hub.
[`docs/index.md`](docs/index.md) is the routing table that maps questions to
the right doc section.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The verify gate is `just verify`
(ruff, pyright strict, pytest).
