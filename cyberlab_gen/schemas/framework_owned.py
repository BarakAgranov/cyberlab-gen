"""Inline declaration of framework-owned fields (ADR 0087).

A *framework-owned* field is one the LLM must never author: the framework stamps, resets, or
derives it (``architecture.md §1.5``). Ownership is declared **on the field** via
``Annotated[T, FrameworkOwned()]`` so every consumer — the refinement patch-path check
(``framework/refinement.py``), the whole-spec reset (``framework/provenance_guard.py``), the
completeness test — derives the same set from one place and cannot drift from the schema. This
supersedes ADR 0086's hand-audit rule + markdown inventory table.

``FrameworkOwned`` is kept extensible: it will later carry a ``mechanism`` field (ADR 0086's
stamp / reset / derive / absent-from-LLM-schema split). Today it carries no fields and tags
exactly the *reset*-mechanism owned fields — those the framework blanks at the extract seam.
"""

from dataclasses import dataclass
from functools import cache

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class FrameworkOwned:
    """Inline marker: the LLM must never author this field; the framework owns it (ADR 0087)."""


@cache
def framework_owned_fields(model: type[BaseModel]) -> frozenset[str]:
    """Names of the fields on ``model`` declared ``Annotated[..., FrameworkOwned()]``.

    Reads the per-field metadata, so it sees ownership declared inline at the field — never a
    second list that could drift from it.
    """
    return frozenset(
        name
        for name, info in model.model_fields.items()
        if any(isinstance(marker, FrameworkOwned) for marker in info.metadata)
    )


__all__ = ["FrameworkOwned", "framework_owned_fields"]
