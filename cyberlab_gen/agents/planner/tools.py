"""The Planner's tools and tool executor (Wave-1 slice).

Architectural source: ``agents.md §5.7`` (the Planner is a **producer** with a read-tool set
distinct from the Extractor's), ``architecture.md §1.5`` (the read/write split is enforced by tool
*availability*), ADR 0089 (the ``ToolUsingAgent`` tool-provider hook), ADR 0078 (read/verify tools).

For the thin slice the Planner's inventory is exactly ``{external_lookup}``:

- It needs the read-only external-source lookup the same way the Extractor does (``agents.md §5.7``:
  "the Planner may need additional lookups during planning").
- It must **not** advertise ``propose_value_type`` / ``propose_thesis_type`` — that authority is the
  **Extractor's alone** (``schema.md §4.16``) — and ``propose_facet`` (the Planner's *own* scoped
  ``runtime:*`` / lab-derived ``lab_class_signal:*`` authority) plus ``query_value_types_registry``
  are **deferred to Task 7**, when the proposal path is generalised. A non-proposing Planner picks
  registered value-types by name from the prompt's registry digest; it has nothing to propose or
  shape-search yet, so wiring those tools now would build an unexercised surface.

The ``external_lookup`` engine itself (NVD resolution, unavailable-source and rate-limit handling,
ADR 0042) is reused from the Extractor's executor rather than duplicated. ``PlannerToolExecutor``
is a read-only subtype so :meth:`ToolUsingAgent._emit`'s return type holds and the Planner reads
``executor.lookups`` off it. The Planner is a **producer**, not a jury: its write tools simply have
not landed yet (Task 7). When Task 9 moves the lookup engine to a neutral ports module (ADR 0077),
this should compose that module directly and drop the subclassing (ADR 0089).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor.tools import (
    ExtractorToolExecutor,
    extractor_tool_definitions,
)

if TYPE_CHECKING:
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import ToolDefinition
    from cyberlab_gen.registries.merge import MergedRegistries


def planner_tool_definitions(
    registered_source_ids: list[str] | None = None,
) -> list[ToolDefinition]:
    """The Planner's advertised tools for the slice: the read-only ``external_lookup`` only.

    This is the **producer-not-jury** read set (``agents.md §5.7``): it deliberately omits the
    Extractor's ``propose_value_type`` / ``propose_thesis_type`` (Extractor authority,
    ``schema.md §4.16``) and the Planner's own ``propose_facet`` + ``query_value_types_registry``
    (deferred to Task 7). ``verify_only`` filters the shared definitions down to exactly
    ``external_lookup`` — reused so the lookup tool's schema stays in one place.
    """
    return extractor_tool_definitions(registered_source_ids, verify_only=True)


class PlannerToolExecutor(ExtractorToolExecutor):
    """Executes the Planner's tool calls (Wave-1 slice: read-only ``external_lookup``).

    Subclasses :class:`~cyberlab_gen.agents.extractor.tools.ExtractorToolExecutor` in read-only
    mode so the ``external_lookup`` engine (NVD / unavailable-source / rate-limit handling,
    ADR 0042) is shared, not duplicated, and so the ``propose_*`` write tools are refused
    defense-in-depth (they are also never advertised — see :func:`planner_tool_definitions`).
    The Planner is a **producer**; this slice simply has no write tools yet (Task 7 adds the scoped
    ``propose_facet``). Reads its lookup trace off the inherited ``lookups`` list.
    """

    def __init__(
        self,
        *,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
    ) -> None:
        super().__init__(registries=registries, nvd_client=nvd_client, verify_only=True)


__all__ = ["PlannerToolExecutor", "planner_tool_definitions"]
