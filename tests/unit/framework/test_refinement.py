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

from cyberlab_gen.framework.provenance_guard import framework_owned_path_buckets
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
    CveReference,
    ExternalRefsBlock,
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
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceFloat, ProvenanceString

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


# --- patch cannot forge framework-owned provenance/ids (ADR 0085) ----------


def _cve_spec() -> AttackSpec:
    """A prior spec carrying one blog_explicit CVE, so a patch can target a CVE field."""
    return _spec().model_copy(
        update={
            "external_references": ExternalRefsBlock(
                cves=[
                    CveReference(
                        cve_id="CVE-2021-44228",  # type: ignore[arg-type]
                        description=_pstr("log4shell"),
                        cvss_score=ProvenanceFloat(
                            value=5.0, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()]
                        ),
                    )
                ]
            )
        }
    )


def test_patch_cannot_forge_framework_provenance_on_flagged_field() -> None:
    # A jury-revise patch is LLM-authored; it must not self-stamp framework_enriched (which evades
    # enrichment's no-op + the grounding search-before-claim check) or the API-override discrepancy
    # record. apply_field_patch scrubs the patch sub-tree before deep-set + whole-spec validation.
    forged: JsonValue = {
        "value": 9.9,
        "source": "external_api",
        "citations": [{"kind": "external_api_response", "reference": "NVD"}],
        "framework_enriched": True,
        "discrepancy_with_blog": True,
        "overridden_blog_value": 5.0,
        "discrepancy_classification": "material",
    }
    merged = apply_field_patch(
        _cve_spec(),
        RefinementPatch(
            patches=[
                FieldPatch(field_path="external_references.cves[0].cvss_score", new_value=forged)
            ]
        ),
    )
    refs = merged.external_references
    assert refs is not None
    score = refs.cves[0].cvss_score
    assert score is not None
    # The value + source the patch legitimately proposed survive; only the framework-only fields
    # the LLM may not author are reset.
    assert score.value == 9.9
    assert score.framework_enriched is False
    assert score.discrepancy_with_blog is False
    assert score.overridden_blog_value is None
    assert score.discrepancy_classification is None


def test_patch_cannot_forge_cve_source_of_record() -> None:
    forged_cve: JsonValue = {
        "cve_id": "CVE-2021-44228",
        "description": {
            "value": "log4shell",
            "source": "blog_explicit",
            "citations": [{"kind": "blog_passage", "reference": "§1"}],
        },
        "source_of_record": "nvd",  # framework-set on a successful lookup only
    }
    merged = apply_field_patch(
        _cve_spec(),
        RefinementPatch(
            patches=[FieldPatch(field_path="external_references.cves[0]", new_value=forged_cve)]
        ),
    )
    refs = merged.external_references
    assert refs is not None
    assert refs.cves[0].source_of_record is None


# --- patch path rejects framework-owned target paths (ADR 0087) ------------
# A bare-leaf / top-level-index patch can carry a framework-owned value stripped of the shape
# `neutralize_patch_provenance` keys off (a scalar, or a top-level index whose entries lack the
# markers), so the value-scrub no-ops and the forgery survives. The path-check rejects any
# field_path that *targets* a framework-owned field — the denylist is generated from the inline
# FrameworkOwned markers, so it cannot drift from the schema.


def test_patch_rejects_bare_leaf_source_of_record() -> None:
    # Hole A: a bare-string source_of_record forges an authoritative-source claim.
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            _cve_spec(),
            RefinementPatch(
                patches=[
                    FieldPatch(
                        field_path="external_references.cves[0].source_of_record", new_value="nvd"
                    )
                ]
            ),
        )


def test_patch_rejects_bare_leaf_framework_enriched() -> None:
    # Hole D: a bare-bool framework_enriched bypasses the enrichment no-op AND the grounding
    # search-before-claim exemption (architecture.md §1.6 mechanical-safety). Rejected by name,
    # regardless of the prior source.
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            _cve_spec(),
            RefinementPatch(
                patches=[
                    FieldPatch(
                        field_path="external_references.cves[0].cvss_score.framework_enriched",
                        new_value=True,
                    )
                ]
            ),
        )


def test_patch_rejects_top_level_material_discrepancies() -> None:
    # Hole B: a top-level-index patch injects a fabricated discrepancy record.
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            _spec(),
            RefinementPatch(
                patches=[FieldPatch(field_path="material_discrepancies", new_value=[])]
            ),
        )


def test_patch_rejects_top_level_reproducibility() -> None:
    # Hole C: a top-level patch forges the framework-derived lab-level block + derivation_trace.
    with pytest.raises(RefinementPathError):
        apply_field_patch(
            _spec(),
            RefinementPatch(patches=[FieldPatch(field_path="reproducibility", new_value=None)]),
        )


def test_patch_allows_legitimate_nested_reproducibility_refine() -> None:
    # The framework-owned `reproducibility` is the AttackSpec top-level block; the per-step
    # ChainStep.reproducibility is authored content and a legitimate refine target. The
    # position-bucketed denylist must NOT reject it (it shares the name, not the ownership).
    new_repro = PerStepReproducibility(
        classification=ReproducibilityTier.PARTIAL_SIMULATION,
        caveats=_pstr("now simulated"),
        why=_pstr("destructive payload"),
    ).model_dump(mode="json", by_alias=True)
    patched = apply_field_patch(
        _spec(),
        RefinementPatch(
            patches=[
                FieldPatch(field_path="chain.chain_steps[0].reproducibility", new_value=new_repro)
            ]
        ),
    )
    assert patched.chain is not None
    assert (
        patched.chain.chain_steps[0].reproducibility.classification
        is ReproducibilityTier.PARTIAL_SIMULATION
    )


def test_patch_check_denylist_matches_declared_markers() -> None:
    # The denylist is generated from the inline FrameworkOwned markers (ADR 0087). Pin it so that
    # neither a new framework-owned field added without a marker, nor a marker removed, can
    # silently change patch-path coverage — the markdown-inventory drift this ADR closes. If this
    # fails, a field's ownership changed: update the marker and this pin together, deliberately.
    root_names, leaf_names = framework_owned_path_buckets()
    assert root_names == {"material_discrepancies", "reproducibility"}
    assert leaf_names == {
        "framework_enriched",
        "discrepancy_with_blog",
        "overridden_blog_value",
        "discrepancy_classification",
        "source_of_record",
    }


def test_every_declared_owned_field_is_rejected_on_the_patch_path() -> None:
    # Both-paths coverage (ADR 0087, item 3): every framework-owned field — the top-level rollups
    # AND the nested leaves — is rejected when a patch targets it. Generated from the markers, so
    # a newly-marked field that is not exercised here makes this test fail (the set assertions).
    root_names, leaf_names = framework_owned_path_buckets()
    for name in root_names:
        with pytest.raises(RefinementPathError):
            apply_field_patch(
                _spec(), RefinementPatch(patches=[FieldPatch(field_path=name, new_value=None)])
            )
    leaf_paths = {
        "framework_enriched": "external_references.cves[0].cvss_score.framework_enriched",
        "discrepancy_with_blog": "external_references.cves[0].cvss_score.discrepancy_with_blog",
        "overridden_blog_value": "external_references.cves[0].cvss_score.overridden_blog_value",
        "discrepancy_classification": (
            "external_references.cves[0].cvss_score.discrepancy_classification"
        ),
        "source_of_record": "external_references.cves[0].source_of_record",
    }
    assert set(leaf_paths) == leaf_names  # every declared leaf is exercised below
    for path in leaf_paths.values():
        with pytest.raises(RefinementPathError):
            apply_field_patch(
                _cve_spec(),
                RefinementPatch(patches=[FieldPatch(field_path=path, new_value=None)]),
            )


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
