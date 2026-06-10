"""The shared mechanical-validator finding/result contract (ADR 0073).

Architectural source: ``validation.md §6.4``/``§6.9``/``§6.10`` (the mechanical validator layers),
``architecture.md §1.6`` (mechanical safety checks are framework-owned, never LLM-based).

Every mechanical validator layer reports the same shape: a list of *findings*, each a ``(code,
location, detail)`` triple with a one-line rendering, wrapped in a *result*. Phase 1 ships two such
layers (the static-schema validator and the orchestrator-owned grounding stack); each had its own
independent ``Finding``/``Result`` pair with divergent ``validate()`` signatures, so every Phase-2
layer (L2/L3/L5) would be a bespoke type-pair. This module gives them one generic base so a new
layer subclasses two parametrised classes, and the locator convention (a JSONPath-like ``location``)
is enforced in exactly one place.

The base is generic over the layer's *code* enum (``Finding[CodeT]``) so each layer keeps its own
closed code vocabulary — a static-schema code and a grounding code never collapse to interchangeable
strings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from cyberlab_gen.schemas.base import InternalModel


class Finding[CodeT: StrEnum](InternalModel):
    """One mechanical-validator finding: a layer ``code``, a field ``location``, and a ``detail``.

    ``location`` uses the JSONPath-like convention shared with ``GapEntry`` so a targeted re-run /
    patch can address the offending field. ``InternalModel`` because findings are consumed in-process
    by the orchestrator (and the jury) and surfaced in the run report; they are not artifacts.
    """

    code: CodeT
    location: str
    detail: str

    def render(self) -> str:
        """A one-line ``code@location: detail`` rendering for logs / the run report."""
        return f"{self.code.value}@{self.location}: {self.detail}"


class FindingResult[F: Finding[Any]](InternalModel):
    """A mechanical-validator layer's findings set (``validation.md §6.9``/``§6.10.2``).

    Generic over the layer's concrete :class:`Finding` subtype. The bound is ``Finding[Any]`` — the
    result is agnostic to *which* code vocabulary the findings carry (that precision lives on
    :class:`Finding[CodeT]`); a result just holds findings and renders them. Subclasses add
    layer-specific derived state (the static layer a ``passed`` flag; the grounding layer
    ``needs_retry`` / ``retry_findings``) but share ``findings`` and its rendering.
    """

    findings: list[F] = Field(default_factory=list[F])

    def rendered_findings(self) -> list[str]:
        """Every finding rendered as a one-line string (for ``ValidationError`` / halt reasons)."""
        return [f.render() for f in self.findings]


__all__ = ["Finding", "FindingResult"]
