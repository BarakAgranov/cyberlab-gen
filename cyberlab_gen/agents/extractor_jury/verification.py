"""Framework-side provenance-mismatch verification (``agents.md §5.5``, ADR 0021).

The jury verifies every ``source`` claim. The *semantic* check ("does the cited
passage actually say this?") is the LLM's job inside the jury prompt. This module
provides the *mechanical* grounding the jury and the orchestrator both rely on:
per source kind, does the provenance envelope have the structure that source
demands, and (for ``external_api``) is there a matching tool call in the trace?

Per-source rules (``agents.md §5.5``):

- ``blog_explicit`` → a ``blog_passage`` citation must be present.
- ``external_api`` → an ``external_api_response`` citation must be present AND a
  matching ``external_lookup`` record must exist in the trace (the jury
  independently re-runs search-before-claim, ``schema.md §4.15``).
- ``llm_inference`` → confidence set + at least one citation.
- ``unknown_from_blog`` → a reason present, no citations.

These are mechanical structure checks (``architecture.md §1.6``: mechanical
safety checks are never LLM-based). The ``Provenance`` model's own validator
already enforces some of these at construction; this walk re-checks across the
whole spec and, crucially, cross-references the *trace* — which the model can't
see — so a structurally valid ``external_api`` field with no backing tool call is
still caught.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource
from cyberlab_gen.schemas.provenance import Provenance

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
    from cyberlab_gen.schemas.attack_spec import AttackSpec


class ProvenanceFinding(InternalModel):
    """One provenance-structure mismatch the verifier found.

    ``ok=False`` findings are returned; the helper returns only mismatches so an
    empty list means "every provenance envelope is structurally grounded".
    """

    field_path: str
    source: str
    detail: str


def verify_provenance(
    spec: AttackSpec,
    lookups: list[ExternalLookupRecord] | None = None,
) -> list[ProvenanceFinding]:
    """Walk every ``Provenance`` in ``spec`` and return per-field mismatches.

    ``lookups`` is the Extractor's tool trace; when ``None`` the external_api
    trace cross-check is skipped (the structural-only check still runs). An empty
    return means all provenance envelopes are well-formed for their source kind.
    """
    findings: list[ProvenanceFinding] = []
    for path, prov in _iter_provenance(spec):
        finding = _check_structure(path, prov)
        if finding is not None:
            findings.append(finding)
    # The external_api trace cross-check needs the CVE id, which the generic walker
    # loses (it uses list indices). Check it explicitly against the CVE refs.
    if lookups is not None:
        findings.extend(_check_api_trace(spec, lookups))
    return findings


def _check_api_trace(
    spec: AttackSpec, lookups: list[ExternalLookupRecord]
) -> list[ProvenanceFinding]:
    """Every external_api CVE field must have a matching nvd lookup in the trace."""
    findings: list[ProvenanceFinding] = []
    if spec.external_references is None:
        return findings
    looked_up = {
        str(rec.params.get("cve_id", "")).strip() for rec in lookups if rec.source_id == "nvd"
    }
    for cve in spec.external_references.cves:
        for label, prov in (("cvss_score", cve.cvss_score), ("severity", cve.severity)):
            if (
                prov is not None
                and prov.source is ProvenanceSource.EXTERNAL_API
                and cve.cve_id not in looked_up
            ):
                findings.append(
                    ProvenanceFinding(
                        field_path=f"external_references.cves[{cve.cve_id}].{label}",
                        source=ProvenanceSource.EXTERNAL_API.value,
                        detail="external_api field has no matching external_lookup call in the trace",
                    )
                )
    return findings


def _check_structure(path: str, prov: Provenance[object]) -> ProvenanceFinding | None:
    src = prov.source
    has_blog = any(c.kind is CitationKind.BLOG_PASSAGE for c in prov.citations)
    has_api = any(c.kind is CitationKind.EXTERNAL_API_RESPONSE for c in prov.citations)

    if src is ProvenanceSource.BLOG_EXPLICIT and not has_blog:
        return ProvenanceFinding(
            field_path=path,
            source=src.value,
            detail="blog_explicit field lacks a blog_passage citation",
        )
    if src is ProvenanceSource.EXTERNAL_API and not has_api:
        return ProvenanceFinding(
            field_path=path,
            source=src.value,
            detail="external_api field lacks an external_api_response citation",
        )
    if src is ProvenanceSource.LLM_INFERENCE:
        if prov.confidence is None:
            return ProvenanceFinding(
                field_path=path, source=src.value, detail="llm_inference field lacks a confidence"
            )
        if not prov.citations:
            return ProvenanceFinding(
                field_path=path, source=src.value, detail="llm_inference field lacks any citation"
            )
    if src is ProvenanceSource.UNKNOWN_FROM_BLOG:
        if not prov.reason:
            return ProvenanceFinding(
                field_path=path, source=src.value, detail="unknown_from_blog field lacks a reason"
            )
        if prov.citations:
            return ProvenanceFinding(
                field_path=path,
                source=src.value,
                detail="unknown_from_blog field must not carry citations",
            )
    return None


def _iter_provenance(value: object, path: str = "") -> list[tuple[str, Provenance[object]]]:
    """Depth-first walk yielding ``(field_path, Provenance)`` for every envelope.

    Recurses through Pydantic models (via ``__dict__``) and lists. ``Provenance``
    instances are yielded and not descended into (their ``value`` may itself be a
    model, but the provenance contract is about the envelope, not nested content).
    """
    from typing import cast

    from pydantic import BaseModel

    out: list[tuple[str, Provenance[object]]] = []
    if isinstance(value, Provenance):
        out.append((path or "<root>", cast("Provenance[object]", value)))
        return out
    if isinstance(value, BaseModel):
        fields = cast("dict[str, object]", value.__dict__)
        for name, field_value in fields.items():
            child_path = f"{path}.{name}" if path else name
            out.extend(_iter_provenance(field_value, child_path))
        return out
    if isinstance(value, list):
        items = cast("list[object]", value)
        for idx, item in enumerate(items):
            out.extend(_iter_provenance(item, f"{path}[{idx}]"))
        return out
    return out


__all__ = ["ProvenanceFinding", "verify_provenance"]
