"""Accept registry proposals into the user overlay (propose→accept→overlay write).

Architectural source: ``schema.md §4.16`` (proposal lifecycle, decision step), ADR
0044. **Mechanical framework code** (no LLM, ``architecture.md §1.5``): it converts
each in-flight ``Proposed*`` to its registry entry, stamps a framework-authored
:class:`ProposalAuditBlock`, and writes it to the overlay via
:func:`cyberlab_gen.registries.overlay_writer.write_overlay_entry`.

Two entry points mirror the two modes (``pipeline.md §3.2.5``):

- :func:`auto_accept_to_overlay` — ``--auto``: batch-accept up to the per-run cap,
  returning what was accepted and what was deferred over the cap. The verb decides
  the over-cap *halt* policy (``errors.ProposalCapExceeded``); this layer only
  splits accepted from deferred and writes the accepted ones.
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
from cyberlab_gen.schemas.registries import FacetEntry, ProposalAuditBlock, ValueTypeEntry

if TYPE_CHECKING:
    from cyberlab_gen.agents.proposals import ProposedFacet, ProposedValueType

#: Registry filenames the acceptance layer writes (the overlay-extensible vocabs).
_VALUE_TYPES_FILE = "value_types"
_FACETS_FILE = "facets"


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


def auto_accept_to_overlay(
    *,
    value_type_proposals: list[ProposedValueType],
    facet_proposals: list[ProposedFacet],
    ctx: AcceptanceContext,
    cap: int,
) -> AcceptanceResult:
    """Auto-accept proposals into the overlay up to ``cap`` total (``--auto``).

    Value-type proposals are accepted first, then facets (a stable order so the same
    proposals are accepted across re-runs). Each accepted entry is marked
    ``approval='auto'``. Proposals beyond the cap are *not* written and returned in
    ``deferred`` for the caller's over-cap halt policy (ADR 0044).
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
    return AcceptanceResult(accepted=accepted, deferred=deferred)


__all__ = [
    "AcceptanceContext",
    "AcceptanceResult",
    "accept_facet",
    "accept_value_type",
    "auto_accept_to_overlay",
]
