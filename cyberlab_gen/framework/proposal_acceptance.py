"""Accept registry proposals into the user overlay (propose→accept→overlay write).

Architectural source: ``schema.md §4.16`` (proposal lifecycle, decision step), ADR 0044, generalized
agent-agnostic in ADR 0099. **Mechanical framework code** (no LLM, ``architecture.md §1.5``): it
converts each in-flight :class:`~cyberlab_gen.agents.proposals.Proposal` to its registry entry, stamps
a framework-authored :class:`ProposalAuditBlock` (with the framework-supplied ``proposed_by`` /
``proposal_origin`` — never agent-authored), mechanically dedups against the merged registry + the
in-flight batch, and writes it to the overlay via
:func:`cyberlab_gen.registries.overlay_writer.write_overlay_entry`.

The single generic path (ADR 0099) replaces the former per-type ``accept_value_type`` /
``accept_facet`` / ``accept_thesis_type`` functions:

- :func:`accept_proposal` — the pure write of one accepted proposal.
- :func:`accept_proposals` — the batch with mechanical dedup + an optional per-run cap.
- :func:`auto_accept_to_overlay` — a thin order-preserving wrapper (value-types → facets →
  thesis-types) kept for the ``--auto`` CLI call site; the verb *reports* the over-cap ``deferred``
  and the dedup ``skipped`` (ADR 0050/0062: neither is a hard halt). The write is gated on the spec
  shipping (the verb calls it post-ship).
"""

from __future__ import annotations

# ``datetime`` / ``Path`` are runtime imports (not TYPE_CHECKING): ``AcceptanceContext`` is a Pydantic
# model whose fields reference them, so Pydantic must resolve the names at class-definition time.
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

# ``ProposerAgent`` is a Pydantic field type on ``AcceptanceContext`` → runtime import. ``proposals``
# imports neither ``framework`` nor the orchestrator, so this introduces no cycle.
from cyberlab_gen.agents.proposals import ProposerAgent
from cyberlab_gen.registries.overlay_writer import write_overlay_entry
from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.registries import (
    FacetEntry,
    ProposalAuditBlock,
    ThesisTypeEntry,
    ValueTypeEntry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from cyberlab_gen.agents.proposals import Proposal
    from cyberlab_gen.registries.merge import MergedRegistries


#: How each concrete overlay entry type maps to its registry: the overlay filename, a human label,
#: and the "already registered?" accessor used for accept-time dedup (ADR 0099). One home for the
#: registry shape so a new proposable vocabulary is one row, not a fourth ``accept_*`` branch.
@dataclass(frozen=True)
class _RegistrySpec:
    filename: str
    label: str
    is_registered: Callable[[MergedRegistries, str], bool]


_ENTRY_REGISTRY: dict[type[ArtifactModel], _RegistrySpec] = {
    ValueTypeEntry: _RegistrySpec(
        "value_types", "value_type", lambda r, k: r.value_type(k) is not None
    ),
    FacetEntry: _RegistrySpec("facets", "facet", lambda r, k: r.facet(k) is not None),
    ThesisTypeEntry: _RegistrySpec(
        "thesis_types", "thesis_type", lambda r, k: r.thesis_type(k) is not None
    ),
}


def _entry_key(entry: ArtifactModel) -> str:
    """The registry key of an overlay entry, via its ``ENTRY_KEY_FIELD`` ClassVar (the one home)."""
    key_field: str = getattr(type(entry), "ENTRY_KEY_FIELD")  # noqa: B009
    key: str = getattr(entry, key_field)
    return key


class AcceptanceContext(InternalModel):
    """The framework-known context stamped into every accepted proposal's audit block.

    ``proposed_by`` / ``proposal_origin`` / ``source_lab`` are framework-recorded, never agent-authored
    (``schema.md §4.16``; ADR 0099): the Extractor builds the context with ``proposed_by="extractor"``,
    ``proposal_origin="llm_during_extraction"``, ``source_lab=None`` (no lab exists at extraction time,
    ADR 0044); a Planner-stage proposal stamps ``"planner"`` / ``"llm_during_planning"``.
    ``proposed_at`` is injected (the caller stamps ``datetime.now(UTC)``) so acceptance is testable
    deterministically.
    """

    overlay_dir: Path
    source_blog: str
    proposed_by_model: str
    proposed_at: datetime
    run_id: str | None = None
    proposed_by: ProposerAgent = "extractor"
    proposal_origin: Literal["llm_during_extraction", "llm_during_planning"] = (
        "llm_during_extraction"
    )
    source_lab: str | None = None


@dataclass(frozen=True)
class AcceptanceResult:
    """What a batch accept wrote vs. deferred over the cap vs. skipped as a mechanical duplicate."""

    accepted: list[str] = field(default_factory=list[str])
    #: Over the per-run cap — bounded steering, surfaced not dropped (ADR 0050/0062).
    deferred: list[str] = field(default_factory=list[str])
    #: A key already in the merged registry or already accepted earlier in this batch — mechanically
    #: rejected so it cannot silently shadow a bundled entry (ADR 0099). Surfaced, not written.
    skipped: list[str] = field(default_factory=list[str])


def _audit(
    ctx: AcceptanceContext, *, reasoning: str, approval: Literal["auto", "human"]
) -> ProposalAuditBlock:
    """Stamp the framework-authored audit block for one accepted proposal."""
    return ProposalAuditBlock(
        proposal_origin=ctx.proposal_origin,
        source_lab=ctx.source_lab,  # type: ignore[arg-type]
        source_blog=ctx.source_blog,  # type: ignore[arg-type]
        proposed_by_model=ctx.proposed_by_model,
        proposed_at=ctx.proposed_at,
        reasoning=reasoning,  # type: ignore[arg-type]
        approval=approval,
    )


def accept_proposal(
    proposal: Proposal, ctx: AcceptanceContext, *, approval: Literal["auto", "human"]
) -> Path:
    """Write one accepted ``proposal`` to the overlay; return the written file path (ADR 0044/0099).

    The pure write — no dedup (that is the batch's mechanical gate). ``proposed_by`` / the run id /
    the audit metadata are stamped from the framework-supplied ``ctx``, never the agent.
    """
    entry = proposal.to_entry(proposed_by=ctx.proposed_by, proposed_in_run=ctx.run_id)
    spec = _ENTRY_REGISTRY[type(entry)]
    return write_overlay_entry(
        overlay_dir=ctx.overlay_dir,
        registry_filename=spec.filename,
        entry_type=type(entry),
        entry=entry,
        audit=_audit(ctx, reasoning=proposal.reasoning, approval=approval),
    )


def accept_proposals(
    proposals: Sequence[Proposal],
    ctx: AcceptanceContext,
    *,
    approval: Literal["auto", "human"],
    registries: MergedRegistries | None = None,
    cap: int | None = None,
) -> AcceptanceResult:
    """Accept a batch of proposals into the overlay with mechanical dedup + an optional cap (ADR 0099).

    Each proposal is, in order: **skipped** if its key already resolves in ``registries`` (a bundled
    collision — overlay-wins would silently shadow it) or was already accepted earlier in this batch
    (the in-flight stale-snapshot case, tracked with a running key-set rather than an expensive
    mid-batch reload); else **deferred** if the per-run ``cap`` of *written* entries is reached
    (bounded steering, surfaced — ADR 0050/0062); else written. ``registries=None`` disables the
    merged-registry check (the intra-batch check always runs), so a caller without a snapshot keeps the
    pre-ADR-0099 behaviour exactly.
    """
    result = AcceptanceResult()
    seen: set[tuple[str, str]] = set()  # (registry filename, key) accepted so far this batch
    for proposal in proposals:
        entry = proposal.to_entry(proposed_by=ctx.proposed_by, proposed_in_run=ctx.run_id)
        spec = _ENTRY_REGISTRY[type(entry)]
        key = _entry_key(entry)
        label = f"{spec.label} {key!r}"
        batch_key = (spec.filename, key)
        if batch_key in seen or (registries is not None and spec.is_registered(registries, key)):
            result.skipped.append(label)
            continue
        if cap is not None and len(result.accepted) >= cap:
            result.deferred.append(label)
            continue
        accept_proposal(proposal, ctx, approval=approval)
        result.accepted.append(label)
        seen.add(batch_key)
    return result


def auto_accept_to_overlay(
    *,
    value_type_proposals: Sequence[Proposal],
    facet_proposals: Sequence[Proposal],
    thesis_type_proposals: Sequence[Proposal],
    ctx: AcceptanceContext,
    cap: int,
    registries: MergedRegistries | None = None,
) -> AcceptanceResult:
    """Auto-accept proposals into the overlay up to ``cap`` total (``--auto``).

    A thin order-preserving wrapper over :func:`accept_proposals`: value-types first, then facets,
    then thesis types (a stable order so the same proposals are accepted across re-runs). Each accepted
    entry is marked ``approval='auto'``. ``registries`` (when supplied) enables accept-time dedup
    against the merged registry; over-cap proposals are ``deferred`` and dedup collisions ``skipped`` —
    both surfaced by the verb, never dropped silently or halted on (ADR 0050/0062/0099).
    """
    return accept_proposals(
        [*value_type_proposals, *facet_proposals, *thesis_type_proposals],
        ctx,
        approval="auto",
        registries=registries,
        cap=cap,
    )


__all__ = [
    "AcceptanceContext",
    "AcceptanceResult",
    "accept_proposal",
    "accept_proposals",
    "auto_accept_to_overlay",
]
