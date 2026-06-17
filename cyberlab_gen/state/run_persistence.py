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

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from cyberlab_gen.providers.base import AgentLabel
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.state.run_store import (
    ENRICHMENT_FILENAME,
    JURY_VERDICT_FILENAME,
    MANIFEST_FILENAME,
    SPEC_FILENAME,
)

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.providers.cost_ledger import CostLedger
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


def _tool_version() -> str | None:
    """The installed ``cyberlab-gen`` version (a SemVer), for ``GenerationBlock.tool_version``.

    ``None`` when the package metadata is unavailable (mirrors ``cli/extract._code_version``); the
    caller then keeps the manifest's existing value rather than stamping an invalid one.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cyberlab-gen")
    except PackageNotFoundError:  # pragma: no cover - always installed under test/CI
        return None


def _stamp_manifest_generation(manifest: LabManifest, ledger: CostLedger) -> LabManifest:
    """Stamp the manifest's framework-owned ``GenerationBlock`` — billed model, tool version, time.

    ``model`` is the **billed** Planner model from the ledger (ADR 0065 — never the LLM self-report);
    ``tool_version`` the installed package version; ``timestamp`` the stamp time. All three of
    ``GenerationBlock``'s fields are framework facts the LLM must not author (ADR 0086 inventory —
    the whole block is framework-stamped, so leaving any to the LLM would be an unguarded hole). Each
    falls back to the manifest's existing value when its source is unavailable (empty ledger / missing
    package metadata), mirroring ``stamp_billed_model``'s no-op-when-nothing-billed. A surgical nested
    ``model_copy``.
    """
    billed = billed_model(ledger, agent_label=AgentLabel.PLANNER)
    tool_version = _tool_version()
    prior = manifest.core.generation
    generation = prior.model_copy(
        update={
            "model": billed if billed is not None else prior.model,
            "tool_version": tool_version if tool_version is not None else prior.tool_version,
            "timestamp": datetime.now(UTC),
        }
    )
    core = manifest.core.model_copy(update={"generation": generation})
    return manifest.model_copy(update={"core": core})


def stamp_framework_provenance[S: SpecEnvelope](spec: S, ledger: CostLedger) -> S:
    """Apply every *stamp-mechanism* framework-owned field in one call, dispatching on artifact type.

    The single STAMP seam before any artifact ships or persists — generic over ``SpecEnvelope`` so it
    is the ONE home for the billed-model + schema-version invariants across **both** artifacts (ADR
    0086/0068: generalize the one stamp home; never copy the billed-model read). Stamp is one of the
    four framework-owned-field mechanisms (ADR 0086: stamp / reset / derive / absent-from-LLM-schema);
    the *reset* mechanism has its own home (``framework/provenance_guard.py``).

    - **schema version** (both artifacts, ADR 0069): ``stamp_spec_version`` (already generic).
    - **billed model** (ADR 0065 — billed, never the LLM self-report): an ``AttackSpec`` carries it on
      ``extraction_metadata.model`` (the Extractor's billed model); a ``LabManifest`` on
      ``core.generation.model`` (the Planner's). Both read the one :func:`billed_model` reader.
    - **generation metadata** (``LabManifest`` only): ``core.generation.{tool_version, timestamp}`` are
      stamped here too (the GenerationBlock is wholly framework-owned).

    Stamp-mechanism fields are intentionally **not** marked ``FrameworkOwned`` inline (a mechanism-less
    marker would mis-drive the reset-walk to blank them; seams §2 / ADR 0087) — the overwrite here is
    their guard, and it runs on every exit path because callers persist from a ``finally``. Idempotent
    except the manifest ``timestamp`` (stamp once at the ship boundary; persistence only re-stamps an
    unmirrored partial).
    """
    spec = stamp_spec_version(spec)
    if isinstance(spec, AttackSpec):
        # The runtime type is preserved (a same-type ``model_copy``), so the cast back to ``S`` is safe.
        return cast("S", stamp_billed_model(spec, ledger))
    if isinstance(spec, LabManifest):
        return cast("S", _stamp_manifest_generation(spec, ledger))
    return spec  # pragma: no cover - no third SpecEnvelope kind exists


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


def persist_plan_artifacts(
    handle: RunHandle,
    *,
    manifest: LabManifest | None,
    verdict: JuryVerdict | None,
    ledger: CostLedger,
    content_hash: str | None = None,
) -> None:
    """Write a ``plan`` run's artifacts + lineage with the **billed** Planner model stamped — one home.

    The Phase-2 sibling of :func:`persist_pipeline_artifacts` (extract). It does **not** copy the
    billed-model invariant: it calls the same :func:`billed_model` reader and the same generalized
    :func:`stamp_framework_provenance` (ADR 0086/0068). The two persist functions are separate only
    because their in-flight state shapes differ (``PipelineState`` vs the plan coordinator's outcome) —
    one shared invariant, two thin callers, not one over-generalized function.

    The manifest persisted is stamped (billed Planner model + version + generation metadata) and written
    only when the ship boundary has not already mirrored it (the ``MANIFEST_FILENAME not in artifacts``
    guard — a clean ship mirrors the stamped manifest to the run dir, so this fills only a
    not-yet-mirrored / halted-manifest case, and never re-stamps a fresh ``timestamp`` over a mirrored
    one). ``lineage.model`` is sourced from the ledger regardless of whether a manifest was produced, so
    the record carries the billed Planner model on **every** exit path (ADR 0068).

    Best-effort by contract: callers invoke this from a ``finally`` and keep their own terminal-status
    resolution + ``finalize``.
    """
    if manifest is not None and MANIFEST_FILENAME not in handle.record.artifacts:
        handle.write_artifact(MANIFEST_FILENAME, stamp_framework_provenance(manifest, ledger))
    if verdict is not None and JURY_VERDICT_FILENAME not in handle.record.artifacts:
        handle.write_artifact(JURY_VERDICT_FILENAME, verdict)
    handle.update_lineage(
        input_hash=content_hash if content_hash else None,
        model=billed_model(ledger, agent_label=AgentLabel.PLANNER),
    )


__all__ = [
    "billed_model",
    "persist_pipeline_artifacts",
    "persist_plan_artifacts",
    "stamp_billed_model",
    "stamp_framework_provenance",
    "stamp_spec_version",
]
