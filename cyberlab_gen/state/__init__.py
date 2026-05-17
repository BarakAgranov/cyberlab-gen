"""State subpackage — local on-disk state under `~/.cyberlab-gen/`.

Manages `config.yaml`, the blog cache, per-run working dirs, telemetry reports,
and the registry overlay directory. Uses `platformdirs` so paths work on
macOS, Linux, and Windows. Architectural source: `docs/architecture.md §2.2`
and `docs/pipeline.md §3.6`.
"""
