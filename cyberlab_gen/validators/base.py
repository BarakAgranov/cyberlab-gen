"""The shared mechanical-validator finding/result contract (ADR 0073).

Architectural source: ``validation.md §6.4``/``§6.9``/``§6.10`` (the mechanical validator layers),
``architecture.md §1.6`` (mechanical safety checks are framework-owned, never LLM-based).

Every mechanical validator layer reports the same shape: a list of *findings*, each a ``(code,
location, detail)`` triple with a one-line rendering, wrapped in a *result*. Phase 1 ships two such
layers (the static-schema validator and the orchestrator-owned grounding stack); each had its own
independent ``Finding``/``Result`` pair with divergent ``validate()`` signatures, so every Phase-2
layer (the semantic cross-check, containerized dry-run, and safety-scan validators) would be a
bespoke type-pair. This module gives them one generic base so a new
layer subclasses two parametrised classes, and the locator convention (a JSONPath-like ``location``)
is enforced in exactly one place.

The base is generic over the layer's *code* enum (``Finding[CodeT]``) so each layer keeps its own
closed code vocabulary — a static-schema code and a grounding code never collapse to interchangeable
strings.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from cyberlab_gen.schemas.base import InternalModel

#: Every ``[...]`` segment in a finding ``location`` — used to enforce integer list indices.
_LIST_INDEX_RE = re.compile(r"\[([^\]]*)\]")


class Finding[CodeT: StrEnum](InternalModel):
    """One mechanical-validator finding: a layer ``code``, a field ``location``, and a ``detail``.

    ``location`` uses the JSONPath-like convention shared with ``GapEntry`` so a targeted re-run /
    patch can address the offending field. ``InternalModel`` because findings are consumed in-process
    by the orchestrator (and the jury) and surfaced in the run report; they are not artifacts.
    """

    code: CodeT
    location: str
    detail: str

    @field_validator("location")
    @classmethod
    def _location_uses_integer_indices(cls, value: str) -> str:
        """Every list index in ``location`` must be an integer (ADR 0074).

        Enforced here, once, for every mechanical-validator layer: a finding locator must be
        addressable by ``framework.refinement._parse_path`` (dotted names + integer list indices)
        so it can feed a targeted patch when a finding drives refinement. A string-id index like
        ``cves[CVE-2024-9999]`` is rejected at construction; the id belongs in ``detail``.
        """
        for raw in _LIST_INDEX_RE.findall(value):
            if not raw.isdigit():
                raise ValueError(
                    f"finding location {value!r} uses a non-integer list index [{raw}]; locators "
                    "use integer list indices so a finding can feed a targeted patch "
                    "(framework.refinement._parse_path)"
                )
        return value

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
