"""Accept registry proposals into the user overlay (propose→accept→overlay write).

Architectural source: ``schema.md §4.16`` (proposal lifecycle, decision step), ADR
0044. **Mechanical framework code** (no LLM, ``architecture.md §1.5``): it converts
each in-flight ``Proposed*`` to its registry entry, stamps a framework-authored
:class:`ProposalAuditBlock`, and writes it to the overlay via
:func:`cyberlab_gen.registries.overlay_writer.write_overlay_entry`.

Two entry points mirror the two modes (``pipeline.md §3.2.5``):

- :func:`auto_accept_to_overlay` — ``--auto``: batch-accept up to the per-run cap,
  returning what was accepted and what was deferred over the cap. The verb *reports*
  the deferred over-cap proposals (ADR 0050/0062: over-cap is bounded steering, not a
  hard halt); this layer only splits accepted from deferred and writes the accepted ones,
  and the write is gated on the spec shipping (the verb calls it post-ship).
- :func:`accept_value_type` / :func:`accept_facet` — ``--interactive``: write one
  reviewed (and possibly user-edited) proposal at a time, marked human-approved.
"""

from __future__ import annotations

# ``datetime`` / ``Path`` are runtime imports (not TYPE_CHECKING): ``AcceptanceContext``
# is a Pydantic model whose fields reference them, so Pydantic must resolve the names
# at class-definition time.
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from cyberlab_gen.registries.overlay_writer import write_overlay_entry
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.registries import (
    FacetEntry,
    ProposalAuditBlock,
    ThesisTypeEntry,
    ValueTypeEntry,
)

if TYPE_CHECKING:
    from cyberlab_gen.agents.proposals import (
        ProposedFacet,
        ProposedThesisType,
        ProposedValueType,
    )

#: Registry filenames the acceptance layer writes (the overlay-extensible vocabs).
_VALUE_TYPES_FILE = "value_types"
_FACETS_FILE = "facets"
_THESIS_TYPES_FILE = "thesis_types"


class AcceptanceContext(InternalModel):
    """The framework-known context stamped into every accepted proposal's audit block.

    ``source_lab`` is intentionally absent — there is no lab at extraction time
    (ADR 0044); the audit block records it as ``None``. ``proposed_at`` is injected
    (the caller stamps ``datetime.now(UTC)``) so acceptance is testable deterministically.
    """

    overlay_dir: Path
    source_blog: str
    proposed_by_model: str
    proposed_at: datetime
    run_id: str | None = None


@dataclass(frozen=True)
class AcceptanceResult:
    """What an ``--auto`` batch accept wrote vs. deferred over the per-run cap."""

    accepted: list[str]
    deferred: list[str]


def _audit(
    ctx: AcceptanceContext, *, reasoning: str, approval: Literal["auto", "human"]
) -> ProposalAuditBlock:
    """Stamp the framework-authored audit block for one accepted proposal."""
    return ProposalAuditBlock(
        proposal_origin="llm_during_extraction",
        source_lab=None,
        source_blog=ctx.source_blog,  # type: ignore[arg-type]
        proposed_by_model=ctx.proposed_by_model,
        proposed_at=ctx.proposed_at,
        reasoning=reasoning,  # type: ignore[arg-type]
        approval=approval,
    )


def accept_value_type(
    proposal: ProposedValueType, ctx: AcceptanceContext, *, approval: Literal["auto", "human"]
) -> Path:
    """Write one accepted value-type proposal to the overlay; return the file path."""
    return write_overlay_entry(
        overlay_dir=ctx.overlay_dir,
        registry_filename=_VALUE_TYPES_FILE,
        entry_type=ValueTypeEntry,
        entry=proposal.to_entry(proposed_in_run=ctx.run_id),
        audit=_audit(ctx, reasoning=proposal.reasoning, approval=approval),
    )


def accept_facet(
    proposal: ProposedFacet, ctx: AcceptanceContext, *, approval: Literal["auto", "human"]
) -> Path:
    """Write one accepted facet proposal to the overlay; return the file path."""
    return write_overlay_entry(
        overlay_dir=ctx.overlay_dir,
        registry_filename=_FACETS_FILE,
        entry_type=FacetEntry,
        entry=proposal.to_entry(),
        audit=_audit(ctx, reasoning=proposal.reasoning, approval=approval),
    )


def accept_thesis_type(
    proposal: ProposedThesisType, ctx: AcceptanceContext, *, approval: Literal["auto", "human"]
) -> Path:
    """Write one accepted thesis-type proposal to the overlay; return the file path (ADR 0045)."""
    return write_overlay_entry(
        overlay_dir=ctx.overlay_dir,
        registry_filename=_THESIS_TYPES_FILE,
        entry_type=ThesisTypeEntry,
        entry=proposal.to_entry(proposed_in_run=ctx.run_id),
        audit=_audit(ctx, reasoning=proposal.reasoning, approval=approval),
    )


def auto_accept_to_overlay(
    *,
    value_type_proposals: list[ProposedValueType],
    facet_proposals: list[ProposedFacet],
    thesis_type_proposals: list[ProposedThesisType],
    ctx: AcceptanceContext,
    cap: int,
) -> AcceptanceResult:
    """Auto-accept proposals into the overlay up to ``cap`` total (``--auto``).

    Value-type proposals are accepted first, then facets, then thesis types (a stable
    order so the same proposals are accepted across re-runs). Each accepted entry is
    marked ``approval='auto'``. Proposals beyond the cap are *not* written and returned
    in ``deferred`` for the caller to **report** (ADR 0050/0062: over-cap is bounded
    steering, not a hard halt — the deferred ones are surfaced, not dropped or halted on).
    """
    accepted: list[str] = []
    deferred: list[str] = []
    for vt in value_type_proposals:
        label = f"value_type {vt.name!r}"
        if len(accepted) < cap:
            accept_value_type(vt, ctx, approval="auto")
            accepted.append(label)
        else:
            deferred.append(label)
    for facet in facet_proposals:
        label = f"facet {facet.name!r}"
        if len(accepted) < cap:
            accept_facet(facet, ctx, approval="auto")
            accepted.append(label)
        else:
            deferred.append(label)
    for thesis in thesis_type_proposals:
        label = f"thesis_type {thesis.name!r}"
        if len(accepted) < cap:
            accept_thesis_type(thesis, ctx, approval="auto")
            accepted.append(label)
        else:
            deferred.append(label)
    return AcceptanceResult(accepted=accepted, deferred=deferred)


__all__ = [
    "AcceptanceContext",
    "AcceptanceResult",
    "accept_facet",
    "accept_thesis_type",
    "accept_value_type",
    "auto_accept_to_overlay",
]
