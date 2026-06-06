"""Extractor tools and the framework-side tool executor.

Architectural source: ``agents.md §5.4`` (Extractor tool inventory),
``schema.md §4.15`` (agent access to external sources), ``§4.16`` (proposal
authority), ADR 0021.

The Extractor has exactly three tools (the brief's item 2):

- ``external_lookup(source_id, params)`` — a read-only call against an
  ``external_data_sources`` registry entry. Phase 1 wires NVD; every other source id
  (registered-but-unwired, or unknown) is recorded as an *unavailable* (not-found)
  lookup so the model proceeds — an unavailable enrichment source is never a fatal
  tool error (ADR 0042). Every call is recorded as an
  ``ExternalLookupRecord`` so the framework can later enforce search-before-claim
  (``schema.md §4.15``): a ``source: external_api`` field with no matching record
  in the trace is rejected.
- ``propose_value_type`` — emits a ``ProposedValueType``. The Extractor is the
  *only* value-type proposer (``schema.md §4.16``).
- ``propose_facet`` — emits a ``ProposedFacet``, but **only** for ``target:*`` or
  blog-derived ``lab_class_signal:*`` facets. A ``runtime:*`` (or any other)
  category is rejected at the tool boundary — that authority is the Planner's.

The Extractor is read-only: no filesystem, no code execution, no URL fetching
outside this tool interface (``agents.md §5.4``). This executor therefore never
opens files or sockets except through the injected ``NvdClient``.

The executor is *framework* code: it collects proposals and the lookup trace as
a side-channel to the agent's typed ``AttackSpec`` output, so proposals and run
mechanics never pollute the artifact (ADR 0021, ``architecture.md §1.5``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from cyberlab_gen.agents.proposals import (
    EXTRACTOR_FACET_CATEGORIES,
    ProposedFacet,
    ProposedValueType,
)
from cyberlab_gen.errors import ExternalApiRateLimitError
from cyberlab_gen.providers.base import ToolCall, ToolDefinition, ToolResult
from cyberlab_gen.schemas.base import InternalModel

if TYPE_CHECKING:
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.registries.merge import MergedRegistries

logger = logging.getLogger(__name__)

TOOL_EXTERNAL_LOOKUP = "external_lookup"
TOOL_PROPOSE_VALUE_TYPE = "propose_value_type"
TOOL_PROPOSE_FACET = "propose_facet"

#: Source id the Phase-1 ``external_lookup`` wires to a live (recordable) client.
_NVD_SOURCE_ID = "nvd"


class ExternalLookupRecord(InternalModel):
    """One ``external_lookup`` tool call the agent made (the search-before-claim trace).

    ``found`` records whether the source returned a record (an agent claiming an
    ``external_api`` value for an id the lookup did NOT find is a hallucination
    the framework can catch). ``params`` is the raw argument dict the agent sent.
    """

    source_id: str
    params: dict[str, Any]
    found: bool
    detail: str


def extractor_tool_definitions() -> list[ToolDefinition]:
    """The three tool schemas advertised to the model (``agents.md §5.4``)."""
    return [
        ToolDefinition(
            name=TOOL_EXTERNAL_LOOKUP,
            description=(
                "Look up an identifier against an authoritative external data source "
                "(e.g. source_id='nvd', params={'cve_id': 'CVE-2024-1234'}). Required "
                "before claiming any external_api-sourced value (search-before-claim)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["source_id", "params"],
            },
        ),
        ToolDefinition(
            name=TOOL_PROPOSE_VALUE_TYPE,
            description=(
                "Propose a new value_types registry entry when the blog mentions a "
                "typed value flowing between phases that no existing type matches."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "value_schema": {"type": "object"},
                    "sensitive": {"type": "boolean"},
                    "notes_for_generator": {"type": "string"},
                    "platforms": {"type": "array", "items": {"type": "string"}},
                    "reasoning": {"type": "string"},
                },
                "required": ["name", "description", "reasoning"],
            },
        ),
        ToolDefinition(
            name=TOOL_PROPOSE_FACET,
            description=(
                "Propose a new target:* or blog-derived lab_class_signal:* facet. "
                "runtime:* and lab-derived facets are the Planner's authority and "
                "are rejected here."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": sorted(EXTRACTOR_FACET_CATEGORIES)},
                    "description": {"type": "string"},
                    "applies_at_levels": {"type": "array", "items": {"type": "string"}},
                    "reasoning": {"type": "string"},
                },
                "required": ["name", "category", "description", "applies_at_levels", "reasoning"],
            },
        ),
    ]


class ExtractorToolExecutor:
    """Executes the Extractor's tool calls; collects proposals + the lookup trace.

    Implements the ``ToolExecutor`` protocol (``provider-interface.md §4.2``). One
    instance is constructed per Extractor run; after the run the framework reads
    ``lookups``, ``value_type_proposals``, and ``facet_proposals`` off it.
    """

    def __init__(
        self,
        *,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
    ) -> None:
        self._registries = registries
        self._nvd_client = nvd_client
        self.lookups: list[ExternalLookupRecord] = []
        self.value_type_proposals: list[ProposedValueType] = []
        self.facet_proposals: list[ProposedFacet] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        """Dispatch one tool call. Unknown tools are an error result, not a raise.

        Returning ``is_error=True`` (rather than raising) keeps the tool-use loop
        alive so the model can recover within its iteration budget.
        """
        if call.tool_name == TOOL_EXTERNAL_LOOKUP:
            return self._external_lookup(call)
        if call.tool_name == TOOL_PROPOSE_VALUE_TYPE:
            return self._propose_value_type(call)
        if call.tool_name == TOOL_PROPOSE_FACET:
            return self._propose_facet(call)
        return ToolResult(
            call_id=call.call_id,
            content=f"unknown tool {call.tool_name!r}",
            is_error=True,
        )

    # --- external_lookup ---------------------------------------------------

    def _external_lookup(self, call: ToolCall) -> ToolResult:
        source_id = str(call.arguments.get("source_id", ""))
        raw_params: object = call.arguments.get("params", {})
        params: dict[str, Any] = (
            {str(k): v for k, v in cast("dict[object, object]", raw_params).items()}
            if isinstance(raw_params, dict)
            else {}
        )

        if source_id == _NVD_SOURCE_ID:
            return self._nvd_lookup(call, params)

        # Any non-NVD source — registered-but-unwired this phase, OR not a known source
        # id at all — is simply UNAVAILABLE. Record it as a not-found lookup and tell the
        # model to proceed (treat the value as requiring external research). This is
        # deliberately NOT an error result (ADR 0042): the provider turns an is_error
        # result into a pydantic-ai ``ModelRetry``, and retrying a lookup against a source
        # that cannot be served is GUARANTEED to fail again — so it exhausts the (default 1)
        # tool-retry budget and escalates to a fatal ``ToolRetryError`` that kills the whole
        # extraction. An unavailable enrichment source must never be fatal; this mirrors the
        # nvd-no-client and rate-limit graceful paths.
        known = self._registries.external_source(source_id) is not None
        availability = "registered but not integrated this phase" if known else "not a known source"
        detail = (
            f"external source {source_id!r} is unavailable ({availability}); treat the value as "
            f"requiring external research (set the field to unknown_from_blog with a reason) "
            f"and continue"
        )
        self.lookups.append(
            ExternalLookupRecord(source_id=source_id, params=params, found=False, detail=detail)
        )
        return ToolResult(call_id=call.call_id, content=detail, is_error=False)

    def _nvd_lookup(self, call: ToolCall, params: dict[str, Any]) -> ToolResult:
        cve_id = str(params.get("cve_id", "")).strip()
        if not cve_id:
            return ToolResult(
                call_id=call.call_id,
                content="external_lookup against nvd requires params.cve_id",
                is_error=True,
            )
        if self._nvd_client is None:
            detail = (
                "nvd lookup unavailable (no client wired); record as requires external research"
            )
            self.lookups.append(
                ExternalLookupRecord(
                    source_id=_NVD_SOURCE_ID, params=params, found=False, detail=detail
                )
            )
            return ToolResult(call_id=call.call_id, content=detail, is_error=False)
        try:
            data = self._nvd_client.lookup_cve(cve_id)
        except ExternalApiRateLimitError as exc:
            # Intentional graceful degradation (pipeline.md §3.2.4): a rate-limit is
            # recorded as a skipped lookup and the run continues, it does not fail.
            # But it is NOT silent — log it so repeated rate-limiting is visible (and
            # consistent with framework/enrichment.py's handler for the same error).
            detail = "external API rate-limited at enrichment time"
            logger.warning("NVD rate-limited looking up %s: %s", cve_id, exc)
            self.lookups.append(
                ExternalLookupRecord(
                    source_id=_NVD_SOURCE_ID, params=params, found=False, detail=detail
                )
            )
            return ToolResult(call_id=call.call_id, content=detail, is_error=False)

        found = data is not None
        detail = (
            json.dumps(data.model_dump(mode="json"))
            if data is not None
            else f"NVD has no record for {cve_id}"
        )
        self.lookups.append(
            ExternalLookupRecord(
                source_id=_NVD_SOURCE_ID, params=params, found=found, detail=detail
            )
        )
        return ToolResult(call_id=call.call_id, content=detail, is_error=False)

    # --- propose_value_type ------------------------------------------------

    def _propose_value_type(self, call: ToolCall) -> ToolResult:
        try:
            proposal = ProposedValueType.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(
                call_id=call.call_id,
                content=f"invalid value_type proposal: {exc.errors()}",
                is_error=True,
            )
        self.value_type_proposals.append(proposal)
        logger.info("extractor proposed value_type %s", proposal.name)
        return ToolResult(
            call_id=call.call_id,
            content=f"value_type proposal '{proposal.name}' recorded for jury/user review",
            is_error=False,
        )

    # --- propose_facet -----------------------------------------------------

    def _propose_facet(self, call: ToolCall) -> ToolResult:
        category = str(call.arguments.get("category", ""))
        if category not in EXTRACTOR_FACET_CATEGORIES:
            # Mechanical authority gate, not LLM discretion (schema.md §4.16).
            return ToolResult(
                call_id=call.call_id,
                content=(
                    f"facet category {category!r} is not the Extractor's authority; "
                    "the Extractor proposes only target:* and blog-derived "
                    "lab_class_signal:* facets (runtime:* and lab-derived facets are "
                    "the Planner's)."
                ),
                is_error=True,
            )
        try:
            proposal = ProposedFacet.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(
                call_id=call.call_id,
                content=f"invalid facet proposal: {exc.errors()}",
                is_error=True,
            )
        self.facet_proposals.append(proposal)
        logger.info("extractor proposed facet %s", proposal.name)
        return ToolResult(
            call_id=call.call_id,
            content=f"facet proposal '{proposal.name}' recorded for jury/user review",
            is_error=False,
        )


__all__ = [
    "TOOL_EXTERNAL_LOOKUP",
    "TOOL_PROPOSE_FACET",
    "TOOL_PROPOSE_VALUE_TYPE",
    "ExternalLookupRecord",
    "ExtractorToolExecutor",
    "extractor_tool_definitions",
]
