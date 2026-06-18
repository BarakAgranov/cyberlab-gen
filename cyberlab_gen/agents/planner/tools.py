"""The Planner's tools and tool executor (the proposing producer; Task 7 / ADR 0099).

Architectural source: ``agents.md §5.7`` (the Planner is a **producer** with a read-tool set distinct
from the Extractor's, plus its OWN scoped ``propose_facet``), ``architecture.md §1.5`` (the read/write
split is enforced by tool *availability*), ``schema.md §4.13``/``§4.16`` (the facet authorship split —
``runtime:*`` and lab-derived ``lab_class_signal:*`` are the Planner's), ADR 0089 (the
``ToolUsingAgent`` tool-provider hook), ADR 0078 (read/verify tools).

The Planner's inventory is ``{external_lookup, propose_facet, query_value_types_registry}``:

- ``external_lookup`` — the read-only external-source lookup the Extractor also uses (``agents.md
  §5.7``: "the Planner may need additional lookups during planning"). The engine (NVD resolution,
  unavailable-source / rate-limit handling, ADR 0042) is **reused** from the Extractor's executor,
  not duplicated.
- ``propose_facet`` — the Planner's OWN scoped proposing authority: ``runtime:*`` and lab-derived
  ``lab_class_signal:*`` only (``PLANNER_FACET_CATEGORIES``). ``value_types`` / ``thesis_types`` and
  ``target:*`` facets are the **Extractor's** authority (``schema.md §4.16``) — withheld from the
  advertisement *and* refused at execution (defense-in-depth).
- ``query_value_types_registry`` — a read tool (deferred from Task 3): returns ``value_types`` shapes
  on demand so the Planner can pick / shape-search an existing typed value. Read-only, never fatal.

``PlannerToolExecutor`` is an :class:`ExtractorToolExecutor` subtype so :meth:`ToolUsingAgent._emit`'s
return type holds and the Planner reads ``executor.lookups`` / ``executor.facet_proposals`` off it. The
per-agent authority is passed as **inputs** (ADR 0099), not new hardcoded literals. When Task 9 moves
the lookup engine to a neutral ports module (ADR 0077), this should compose that module directly and
drop the subclassing (ADR 0089).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from cyberlab_gen.agents.extractor.tools import (
    TOOL_PROPOSE_THESIS_TYPE,
    TOOL_PROPOSE_VALUE_TYPE,
    ExtractorToolExecutor,
    external_lookup_definition,
    propose_facet_definition,
)
from cyberlab_gen.agents.proposals import PLANNER_FACET_CATEGORIES
from cyberlab_gen.providers.base import ToolCall, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.registries.merge import MergedRegistries

logger = logging.getLogger(__name__)

#: The Planner's on-demand value_types read tool (deferred from Task 3; ADR 0099).
TOOL_QUERY_VALUE_TYPES = "query_value_types_registry"

#: The Planner's facet-authority hint, surfaced when a proposal's category is out of authority.
_PLANNER_FACET_AUTHORITY_HINT = (
    "The Planner proposes only runtime:* and lab-derived lab_class_signal:* facets; value_types, "
    "thesis_types and target:* facets are the Extractor's."
)

#: ``propose_*`` write tools the Planner refuses at execution (Extractor authority, ``schema.md
#: §4.16``) — defense-in-depth behind the withheld advertisements.
_PLANNER_REFUSED_PROPOSE_TOOLS = frozenset({TOOL_PROPOSE_VALUE_TYPE, TOOL_PROPOSE_THESIS_TYPE})


def query_value_types_definition() -> ToolDefinition:
    """The ``query_value_types_registry`` read tool schema (ADR 0099)."""
    return ToolDefinition(
        name=TOOL_QUERY_VALUE_TYPES,
        description=(
            "Look up the value_types registry on demand. Omit 'name' to list all registered "
            "value-type names + descriptions; pass 'name' to get that type's full shape (schema, "
            "sensitivity, platforms). Read-only — use it to pick or shape-search an existing typed "
            "value rather than guessing one (the Planner does not propose value_types — that is the "
            "Extractor's authority)."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": [],
        },
    )


def planner_tool_definitions(
    registered_source_ids: list[str] | None = None,
) -> list[ToolDefinition]:
    """The Planner's advertised tools: ``external_lookup`` + scoped ``propose_facet`` + the value-type
    query (``agents.md §5.7``; Task 7 / ADR 0099).

    The ``propose_facet`` category enum is exactly ``PLANNER_FACET_CATEGORIES`` (``runtime`` /
    ``lab_class_signal``) — never the Extractor's ``target`` — so the read/write authority split is
    enforced by tool *availability* (``architecture.md §1.5``). ``propose_value_type`` /
    ``propose_thesis_type`` are not advertised at all (Extractor authority, ``schema.md §4.16``).
    """
    return [
        external_lookup_definition(registered_source_ids),
        propose_facet_definition(
            PLANNER_FACET_CATEGORIES,
            description=(
                "Propose a new runtime:* or lab-derived lab_class_signal:* facet (the Planner's "
                "authority) when the planned lab needs one no existing facet matches. target:* "
                "facets and value_types are the Extractor's authority and are rejected here."
            ),
        ),
        query_value_types_definition(),
    ]


class PlannerToolExecutor(ExtractorToolExecutor):
    """Executes the Planner's tool calls: ``external_lookup`` + scoped ``propose_facet`` + the query.

    Subclasses :class:`~cyberlab_gen.agents.extractor.tools.ExtractorToolExecutor` so the
    ``external_lookup`` engine (NVD / unavailable-source / rate-limit handling, ADR 0042) and the
    ``propose_facet`` collection are shared, not duplicated. The per-agent authority is passed as
    inputs (ADR 0099): the Planner's facet categories, its authority hint, and the ``propose_*`` tools
    it refuses (value-type / thesis-type — Extractor authority). It is **not** ``verify_only`` (it is a
    producer that proposes). Reads its lookup trace + facet proposals off the inherited lists.
    """

    def __init__(
        self,
        *,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
    ) -> None:
        super().__init__(
            registries=registries,
            nvd_client=nvd_client,
            facet_categories=PLANNER_FACET_CATEGORIES,
            facet_authority_hint=_PLANNER_FACET_AUTHORITY_HINT,
            refused_propose_tools=_PLANNER_REFUSED_PROPOSE_TOOLS,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        """Dispatch ``query_value_types_registry`` here; delegate everything else to the base executor.

        Handling the new read tool in the override keeps the base Extractor executor byte-unchanged
        (it never advertises or serves the query); the shared ``external_lookup`` / ``propose_facet``
        paths run via ``super().execute`` with the Planner's authority already configured.
        """
        if call.tool_name == TOOL_QUERY_VALUE_TYPES:
            return self._query_value_types(call)
        return await super().execute(call)

    def _query_value_types(self, call: ToolCall) -> ToolResult:
        """Serve the on-demand value_types lookup — read-only, never a fatal error result (ADR 0042/0099).

        A ``name`` argument returns that entry's full shape; absent (or unknown), it returns the
        registry listing so the Planner can pick. A miss is informative content, not an error: like
        ``external_lookup``, an ``is_error`` here would become a ``ModelRetry`` and could escalate to a
        fatal ``ToolRetryError`` over an optional read.
        """
        name = str(call.arguments.get("name", "")).strip()
        if name:
            entry = self._registries.value_type(name)
            if entry is not None:
                detail: dict[str, Any] = {
                    "name": entry.name,
                    "description": entry.description,
                    "schema": entry.schema_,
                    "sensitive": entry.sensitive,
                    "platforms": list(entry.platforms),
                }
                return ToolResult(call_id=call.call_id, content=json.dumps(detail), is_error=False)
            # A miss: steer the model to the available names instead of failing.
            content = f"no value type named {name!r}; registered value types: {self._value_type_listing()}"
            return ToolResult(call_id=call.call_id, content=content, is_error=False)
        return ToolResult(call_id=call.call_id, content=self._value_type_listing(), is_error=False)

    def _value_type_listing(self) -> str:
        """A JSON array of ``{name, description}`` for every registered value type."""
        listing = [
            {"name": e.name, "description": e.description}
            for e in self._registries.value_types.entries
        ]
        return json.dumps(listing)


__all__ = ["TOOL_QUERY_VALUE_TYPES", "PlannerToolExecutor", "planner_tool_definitions"]
