"""Tests for targeted-patch refinement (ADR 0048 A1, ADR 0054).

The patch primitive is pure framework: it deep-sets a ``RefinementPatch`` (a list of
``{field_path, new_value}``) onto a copy of the prior ``AttackSpec`` and re-validates the
whole spec. These tests pin the load-bearing properties without a provider:

* **non-regression / convergence by construction** — only flagged field paths change;
  every other field (value *and* inline provenance) is byte-identical to the prior dump.
  This is the property that makes the jury-revise loop converge instead of bouncing
  9→6→9→10 (a patch cannot regress a field nobody flagged).
* **whole-spec re-validation (R2)** — a patch that breaks a cross-field invariant is caught
  by ``AttackSpec.model_validate``, not silently accepted.
* **path discipline** — a path that doesn't resolve in the prior spec, or uses a
  non-integer ``[id]`` index, is rejected (``RefinementPathError``) rather than silently
  creating/guessing a field.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.framework.refinement import (
    FieldPatch,
    RefinementPatch,
    RefinementPathError,
    apply_field_patch,
)
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    ExtractionMetadataBlock,
    PerStepReproducibility,
    PublisherBlock,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityTier,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString

if TYPE_CHECKING:
    from pydantic import JsonValue

_HASH = "a" * 64


# --- builders --------------------------------------------------------------


def _cite(ref: str = "§1") -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference=ref)


def _pstr(value: str, *, ref: str = "§1") -> ProvenanceString:
    return ProvenanceString(
        value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite(ref)]
    )


def _spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=SourceBlock(
            url="https://example.com/blog",  # type: ignore[arg-type]
            canonical_url="https://example.com/blog",  # type: ignore[arg-type]
            title="A writeup",
            publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
            fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
            content_hash=_HASH,
            fetch_method="httpx",
            word_count=100,
        ),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=ThesisBlock(
            types=["vulnerability_chain"],  # type: ignore[list-item]
            summary=_pstr("a vague chain", ref="§1"),
            attacker_objective=_pstr("admin", ref="§1"),
            vulnerability_story=_pstr("misconfig", ref="§1"),
            duration_as_described=_pstr("a week", ref="§1"),
        ),
        facets=["target:aws"],  # type: ignore[list-item]
        chain=ChainBlock(
            chain_steps=[
                ChainStep(
                    id="step-1",  # type: ignore[arg-type]
                    step_number=1,
                    title="Step 1",
                    description=_pstr("do the first thing", ref="§2"),
                    blog_excerpt="verbatim one",
                    techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
                    reproducibility=PerStepReproducibility(
                        classification=ReproducibilityTier.FULL,
                        caveats=_pstr("none"),
                        why=_pstr("scriptable"),
                    ),
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                ),
                ChainStep(
                    id="step-2",  # type: ignore[arg-type]
                    step_number=2,
                    title="Step 2",
                    description=_pstr("do the second thing", ref="§3"),
                    blog_excerpt="verbatim two",
                    techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
                    reproducibility=PerStepReproducibility(
                        classification=ReproducibilityTier.FULL,
                        caveats=_pstr("none"),
                        why=_pstr("scriptable"),
                    ),
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                ),
            ]
        ),
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0", model="m", completeness_score=0.8, citations_count=2
        ),
    )


def _dump(spec: AttackSpec) -> dict[str, object]:
    return spec.model_dump(mode="json", by_alias=True)


def _prov_value(value: str, *, ref: str) -> JsonValue:
    """A fresh blog_explicit Provenance[str] sub-tree, as the model would emit it."""
    return _pstr(value, ref=ref).model_dump(mode="json", by_alias=True)


# --- round-trip ------------------------------------------------------------


def test_refinement_patch_round_trips_with_nested_new_value() -> None:
    patch = RefinementPatch(
        patches=[
            FieldPatch(field_path="thesis.summary", new_value=_prov_value("sharper", ref="§4"))
        ]
    )
    restored = RefinementPatch.model_validate(patch.model_dump())
    assert restored == patch
    assert isinstance(restored.patches[0].new_value, dict)
    assert restored.patches[0].new_value["value"] == "sharper"  # type: ignore[index]


# --- convergence / non-regression ------------------------------------------


def test_patch_replaces_only_the_flagged_shallow_field() -> None:
    prior = _spec()
    new_summary = _prov_value("a precise, well-cited chain summary", ref="§5")
    patched = apply_field_patch(
        prior,
        RefinementPatch(patches=[FieldPatch(field_path="thesis.summary", new_value=new_summary)]),
    )

    assert patched.thesis is not None
    assert patched.thesis.summary.value == "a precise, well-cited chain summary"

    # Non-regression: overwrite ONLY the flagged path in the prior dump; the two dumps
    # must then be byte-identical — proof nothing else moved.
    prior_dump = _dump(prior)
    patched_dump = _dump(patched)
    prior_dump["thesis"]["summary"] = patched_dump["thesis"]["summary"]  # type: ignore[index]
    assert patched_dump == prior_dump


def test_patch_replaces_only_the_flagged_deep_field() -> None:
    prior = _spec()
    new_desc = _prov_value("a corrected step-1 description", ref="§6")
    patched = apply_field_patch(
        prior,
        RefinementPatch(
            patches=[FieldPatch(field_path="chain.chain_steps[0].description", new_value=new_desc)]
        ),
    )

    assert patched.chain is not None
    assert patched.chain.chain_steps[0].description.value == "a corrected step-1 description"
    # siblings, the other step, and every other block are untouched
    assert patched.chain.chain_steps[1].description.value == "do the second thing"

    prior_dump = _dump(prior)
    patched_dump = _dump(patched)
    prior_dump["chain"]["chain_steps"][0]["description"] = patched_dump["chain"]["chain_steps"][0][  # type: ignore[index]
        "description"
    ]
    assert patched_dump == prior_dump


def test_patch_updates_flagged_provenance_and_leaves_unflagged_provenance_identical() -> None:
    # Point 6: a patched field carries the agent's NEW provenance (source + citations);
    # an untouched field keeps its original provenance byte-identical.
    prior = _spec()
    # new provenance: a different citation reference and an llm_inference source
    new_summary = ProvenanceString(
        value="an inferred summary",
        source=ProvenanceSource.LLM_INFERENCE,
        confidence=0.8,
        confidence_source="model_self_reported",  # type: ignore[arg-type]
        citations=[_cite("§9")],
    ).model_dump(mode="json", by_alias=True)
    patched = apply_field_patch(
        prior,
        RefinementPatch(patches=[FieldPatch(field_path="thesis.summary", new_value=new_summary)]),
    )

    assert patched.thesis is not None
    assert prior.thesis is not None
    assert patched.thesis.summary.source is ProvenanceSource.LLM_INFERENCE
    assert patched.thesis.summary.citations[0].reference == "§9"
    # an unflagged content field keeps its original provenance exactly
    assert (
        patched.thesis.attacker_objective.model_dump()
        == prior.thesis.attacker_objective.model_dump()
    )


def test_patch_applies_multiple_fields_and_nothing_else_moves() -> None:
    prior = _spec()
    patched = apply_field_patch(
        prior,
        RefinementPatch(
            patches=[
                FieldPatch(field_path="thesis.summary", new_value=_prov_value("s", ref="§7")),
                FieldPatch(
                    field_path="chain.chain_steps[1].description",
                    new_value=_prov_value("d2", ref="§8"),
                ),
            ]
        ),
    )
    prior_dump = _dump(prior)
    patched_dump = _dump(patched)
    prior_dump["thesis"]["summary"] = patched_dump["thesis"]["summary"]  # type: ignore[index]
    prior_dump["chain"]["chain_steps"][1]["description"] = patched_dump["chain"]["chain_steps"][1][  # type: ignore[index]
        "description"
    ]
    assert patched_dump == prior_dump


# --- whole-spec re-validation (R2) -----------------------------------------


def test_patch_revalidates_whole_spec_and_catches_cross_field_break() -> None:
    # A blog_explicit provenance with no citations breaks Provenance._source_rules — a
    # whole-spec invariant. model_validate (not a patched-fields-only check) must catch it.
    prior = _spec()
    bad_value: JsonValue = {"value": "x", "source": "blog_explicit", "citations": []}
    with pytest.raises(PydanticValidationError):
        apply_field_patch(
            prior,
            RefinementPatch(patches=[FieldPatch(field_path="thesis.summary", new_value=bad_value)]),
        )


def test_patch_revalidation_catches_list_level_invariant_break() -> None:
    # Re-ordering chain steps via a patch breaks ChainBlock._step_numbers_monotonic — a
    # cross-field, list-level invariant only a whole-spec re-validation catches.
    prior = _spec()
    # set step-1's step_number to 5, making the [5, 2] sequence non-monotonic
    step0 = prior.chain.chain_steps[0].model_dump(mode="json", by_alias=True)  # type: ignore[union-attr]
    step0["step_number"] = 5
    with pytest.raises(PydanticValidationError):
        apply_field_patch(
            prior,
            RefinementPatch(
                patches=[FieldPatch(field_path="chain.chain_steps[0]", new_value=step0)]
            ),
        )


# --- path discipline -------------------------------------------------------


def test_patch_rejects_a_path_that_does_not_resolve() -> None:
    prior = _spec()
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            prior,
            RefinementPatch(patches=[FieldPatch(field_path="thesis.no_such_field", new_value="x")]),
        )


def test_patch_rejects_an_out_of_range_list_index() -> None:
    prior = _spec()
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            prior,
            RefinementPatch(
                patches=[
                    FieldPatch(
                        field_path="chain.chain_steps[9].description",
                        new_value=_prov_value("x", ref="§1"),
                    )
                ]
            ),
        )


def test_patch_rejects_a_non_integer_index_segment() -> None:
    # Phase-1 convention is dotted + integer index; a string id like [step-1] is rejected
    # (not silently resolved) — the producer-convention drift is canonicalized under A3/B1.
    prior = _spec()
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            prior,
            RefinementPatch(
                patches=[
                    FieldPatch(
                        field_path="chain.chain_steps[step-1].description",
                        new_value=_prov_value("x", ref="§1"),
                    )
                ]
            ),
        )
