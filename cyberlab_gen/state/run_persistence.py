"""The shared run persistence/lineage service — the single home for the billed-model invariant.

Both the ``extract`` CLI (``cli/extract.py``) and the eval harness (``eval/runner/runner.py``)
persist a pipeline run's per-stage artifacts + lineage into the run store (ADR 0039/0053). The
choreography is identical, and the architectural invariant it must hold is non-negotiable: the
framework records the **billed** provider model (from the cost ledger), never the LLM-authored
``extraction_metadata.model`` (``architecture.md §1.5``; ADR 0065 — a real run self-reported
``"claude-sonnet"`` while the ledger billed ``claude-opus-4-8``).

Keeping that invariant in two parallel call sites is exactly how the eval sibling drifted and
re-leaked the self-report on its halt/crash paths (investigation 0002 §1.5; ADR 0068): its
``_persist_run_dir`` stamped nothing and read ``str(meta.model)``. This module is the one seam both
callers consume, so the invariant has a single home and Phase-2's ``generate`` verb inherits the
correct behaviour by construction rather than copying a third time.

What stays caller-side: terminal-status resolution and ``handle.finalize`` — those genuinely differ
(the CLI classifies from ``sys.exc_info()``; the eval passes an explicit status), so this service
deliberately does not own them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.providers.base import AgentLabel
from cyberlab_gen.state.run_store import (
    ENRICHMENT_FILENAME,
    JURY_VERDICT_FILENAME,
    SPEC_FILENAME,
)

if TYPE_CHECKING:
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.envelope import SpecEnvelope
    from cyberlab_gen.state.run_store import RunHandle


def billed_model(
    ledger: CostLedger, *, agent_label: AgentLabel = AgentLabel.EXTRACTOR
) -> str | None:
    """The provider model the framework actually billed — the authoritative provenance source.

    ``lineage.model`` and ``extraction_metadata.model`` must come from the billed cost ledger,
    **never** from the LLM-authored ``extraction_metadata.model`` (``architecture.md §1.5``; ADR
    0065). Prefers the last entry for ``agent_label`` (the model that produced the artifact); falls
    back to the last billed entry; ``None`` on an empty ledger (nothing billed yet). The
    ``agent_label`` parameter generalises the former extractor-only reader so Phase-2 generators
    can record their own billed model through the same seam.
    """
    labelled = [e for e in ledger.entries if e.agent_label is agent_label]
    pool = labelled or ledger.entries
    return pool[-1].model if pool else None


def stamp_billed_model(spec: AttackSpec, ledger: CostLedger) -> AttackSpec:
    """Return ``spec`` with ``extraction_metadata.model`` framework-stamped to the billed model.

    A surgical ``model_copy`` (every other field byte-identical). No-op (keeps the spec's own
    value as a last-resort fallback) when nothing is billed yet — the ledger is non-empty in
    practice, so the spec value is never the real shipped model. Idempotent: stamping an
    already-stamped spec with the same ledger yields the same billed model.
    """
    billed = billed_model(ledger)
    if billed is None:
        return spec
    return spec.model_copy(
        update={
            "extraction_metadata": spec.extraction_metadata.model_copy(update={"model": billed})
        }
    )


def stamp_spec_version[S: SpecEnvelope](spec: S) -> S:
    """Return ``spec`` with ``spec_version`` framework-stamped to its kind's ``CURRENT_VERSION``.

    The schema version is a framework fact, not LLM content (``architecture.md §1.5``; ADR 0069):
    the model emits a value (floor ``ge=1``), but the framework overrides it so everything written
    to disk carries the current version — and the load gate (``architecture.md §0.6``) can then
    refuse anything else without ever migrating. Per-kind (ADR 0080): an ``AttackSpec`` is stamped to
    ``CURRENT_ATTACK_SPEC_VERSION``, a ``LabManifest`` to ``CURRENT_MANIFEST_VERSION`` — read off
    ``type(spec).CURRENT_VERSION``. Idempotent; a surgical ``model_copy``.
    """
    current = type(spec).CURRENT_VERSION
    if spec.spec_version == current:
        return spec
    return spec.model_copy(update={"spec_version": current})


def stamp_framework_provenance(spec: AttackSpec, ledger: CostLedger) -> AttackSpec:
    """Apply every *stamp-mechanism* framework-owned field in one call.

    The single STAMP seam before a spec ships or persists: the billed model (ADR 0065) and the
    schema version (ADR 0069). Stamp is one of the four framework-owned-field mechanisms (ADR 0086:
    stamp / reset / derive / absent-from-LLM-schema); the *reset* mechanism has its own home
    (``framework/provenance_guard.py``). Ownership itself is declared inline on each field
    (``FrameworkOwned``, ADR 0087), so a new stamp-mechanism field is added here and marked there.
    Callers use this rather than the individual stampers so a stamp-seam field is never forgotten.
    """
    return stamp_spec_version(stamp_billed_model(spec, ledger))


def persist_pipeline_artifacts(
    handle: RunHandle,
    *,
    state: PipelineState | None,
    shipped_spec: AttackSpec | None,
    ledger: CostLedger,
    content_hash: str | None,
) -> None:
    """Write a run's per-stage artifacts + lineage with the **billed** model stamped — one home.

    The spec persisted is ``shipped_spec`` when present (a clean ship), else the runner's last
    (partial) ``state.spec``; either way it is billed-model-stamped before persistence, so the run
    record never carries the LLM self-report on **any** exit path — the leak the eval sibling had
    on its halt/crash paths (ADR 0068). ``lineage.model`` is sourced from the ledger regardless of
    whether a spec was emitted; ``extractor_version`` is legitimately spec-authored config
    provenance; ``input_hash`` is the ingested content hash (``update_lineage`` ignores ``None``
    fields, so an empty ledger never clears a known value). A clean ship may have already mirrored
    the post-edit spec — only the gap is filled, never overwritten.

    Best-effort by contract: callers invoke this from a ``finally`` and keep their own
    terminal-status resolution + ``finalize``.
    """
    spec = shipped_spec if shipped_spec is not None else (state.spec if state is not None else None)
    if spec is not None:
        spec = stamp_framework_provenance(spec, ledger)
        # A clean ship already mirrored the post-edit spec; only fill in the partial.
        if SPEC_FILENAME not in handle.record.artifacts:
            handle.write_artifact(SPEC_FILENAME, spec)
        # extractor_version is config/code provenance (legitimately spec-authored); model is NOT
        # taken from the spec — it is the billed provider model, sourced below from the ledger.
        handle.update_lineage(extractor_version=str(spec.extraction_metadata.extractor_version))
    if state is not None:
        if state.verdict is not None:
            handle.write_artifact(JURY_VERDICT_FILENAME, state.verdict)
        if state.enrichment is not None:
            handle.write_artifact(ENRICHMENT_FILENAME, state.enrichment)
    handle.update_lineage(
        input_hash=content_hash if content_hash else None,
        model=billed_model(ledger),
    )


__all__ = [
    "billed_model",
    "persist_pipeline_artifacts",
    "stamp_billed_model",
    "stamp_framework_provenance",
    "stamp_spec_version",
]
