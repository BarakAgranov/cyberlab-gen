"""Tests for registry meta-schemas.

Architectural source: ``schema-details.md`` §6; cross-checked against
``registry-details.md`` §2-§7. The example fixtures below mirror the
canonical seeds the architecture documents promise.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from cyberlab_gen.schemas import (
    BundledRegistryFile,
    CacheConfig,
    DiscrepancyMaterialityRule,
    EnrichmentTrigger,
    ExecutionContextEntry,
    ExecutionContextsRegistry,
    ExternalDataSourceEntry,
    ExternalDataSourcesRegistry,
    ExternalSourceEndpoint,
    ExternalSourceParam,
    FacetEntry,
    FacetsRegistry,
    LabCredentialEntry,
    LabCredentialsRegistry,
    OverlayRegistryFile,
    ProposalAuditBlock,
    RateLimit,
    StaticCatalogEntry,
    StaticCatalogsRegistry,
    ValueTypeEntry,
    ValueTypesRegistry,
)

# --- Factory helpers -------------------------------------------------------


def _rate_limit() -> RateLimit:
    return RateLimit(without_key="5 requests per 30 seconds")


def _cache(scope: str = "per-key") -> CacheConfig:
    return CacheConfig(ttl=timedelta(days=7), scope=scope)  # type: ignore[arg-type]


def _endpoint(endpoint_id: str = "lookup_cve") -> ExternalSourceEndpoint:
    return ExternalSourceEndpoint(
        id=endpoint_id,
        method="GET",
        path_template="?cveId={cve_id}",
        parameters={
            "cve_id": ExternalSourceParam(
                type="string",
                required=True,
                pattern="^CVE-[0-9]{4}-[0-9]+$",
            ),
        },
        response_schema_ref="nvd_cve_response_v2",
        cache_ttl=timedelta(days=7),
    )


def _external_data_source(source_id: str = "nvd") -> ExternalDataSourceEntry:
    """A valid `nvd`-shaped entry (the §4.2 canonical first example)."""
    return ExternalDataSourceEntry(
        id=source_id,
        name="National Vulnerability Database",
        description="Authoritative CVE metadata cross-cloud.",
        base_url="https://services.nvd.nist.gov/rest/json/cves/2.0",  # type: ignore[arg-type]
        auth_type="optional_api_key",
        auth_env_var="NVD_API_KEY",
        rate_limit=_rate_limit(),
        endpoints=[_endpoint()],
        cache=_cache(),
        best_effort=False,
        enrichment_triggers=[
            EnrichmentTrigger(
                field="chain.chain_steps[*].techniques.mitre[*].cve_ids[*]",
                action="lookup",
                endpoint="lookup_cve",
            )
        ],
        discrepancy_materiality_rules=[
            DiscrepancyMaterialityRule(
                field_path="cvss_score",
                classification="material",
                rule_description="CVSS contradiction changes severity narrative.",
            )
        ],
        notes_for_extractor="Authoritative for CVE metadata.",
    )


def _static_catalog(catalog_id: str = "aws_iam_catalog") -> StaticCatalogEntry:
    """A valid `aws_iam_catalog`-shaped entry (the §5.2 canonical example).

    ``path_template`` is non-empty here to exercise the canonical AWS asset
    suffix; per ADR 0007 the field accepts empty strings too (RSS feeds and
    several static catalogs encode the full URL in ``base_url``).
    """
    return StaticCatalogEntry(
        id=catalog_id,
        name="AWS IAM Action Catalog",
        description="Static JSON of all AWS IAM actions.",
        base_url="https://awspolicygen.s3.amazonaws.com/",  # type: ignore[arg-type]
        auth_type="none",
        rate_limit=RateLimit(without_key="static asset; polling-friendly"),
        endpoints=[
            ExternalSourceEndpoint(
                id="catalog_download",
                method="GET",
                path_template="/js/policies.js",
                parameters={},
                response_schema_ref="aws_iam_actions_catalog",
                cache_ttl=timedelta(days=30),
            )
        ],
        cache=_cache(scope="global"),
        best_effort=False,
        notes_for_generator="Consulted via lookup_cloud_iam_action(...).",
    )


def _value_type(name: str = "aws_credentials") -> ValueTypeEntry:
    """The §2.1 canonical `aws_credentials` example."""
    return ValueTypeEntry.model_validate(
        {
            "name": name,
            "description": "Long-lived AWS access key plus secret access key pair.",
            "schema": {
                "type": "object",
                "required": ["access_key_id", "secret_access_key"],
                "properties": {
                    "access_key_id": {
                        "type": "string",
                        "pattern": "^(AKIA|ASIA)[0-9A-Z]{16}$",
                    },
                    "secret_access_key": {"type": "string", "minLength": 40},
                },
            },
            "sensitive": True,
            "examples": [{"access_key_id": "AKIAIOSFODNN7EXAMPLE"}],
            "platforms": ["aws"],
        }
    )


def _facet(name: str = "target:aws") -> FacetEntry:
    """The §3.2 canonical `target:aws` example."""
    return FacetEntry(
        name=name,  # type: ignore[arg-type]
        category="target",
        proposed_by="extractor",
        description="Attack targets AWS (services, IAM, accounts, infrastructure).",
        applies_at_levels=["lab", "phase"],
    )


def _execution_context(name: str = "attacker_local") -> ExecutionContextEntry:
    """The §6.2 canonical `attacker_local` example."""
    return ExecutionContextEntry(
        name=name,
        description="Code runs on the attacker's local machine.",
        credential_assumption="Lab's configured cloud credentials via standard tooling.",
        network_assumption="Outbound to target cloud APIs.",
    )


def _lab_credential(cred_id: str = "aws_test_access_key") -> LabCredentialEntry:
    """The §7.5 canonical `aws_test_access_key` example."""
    return LabCredentialEntry(
        id=cred_id,
        platform="aws",
        description="Canonical fake AWS access key used in AWS examples.",
        pattern="^AKIAIOSFODNN7EXAMPLE$",
        example="AKIAIOSFODNN7EXAMPLE",
        whitelist_rationale="Documented AWS example access key; never a real credential.",
    )


def _proposal_audit() -> ProposalAuditBlock:
    return ProposalAuditBlock(
        proposal_origin="llm_during_extraction",
        source_lab="example_lab",
        source_blog="https://example.com/blog",  # type: ignore[arg-type]
        proposed_by_model="claude-opus-4-7",
        proposed_at=datetime(2026, 5, 18, tzinfo=UTC),
        reasoning="Blog describes harvesting JWT tokens.",
    )


# --- Per-entry happy paths + extra-rejection -------------------------------


def test_value_type_entry_constructs_canonical_example() -> None:
    entry = _value_type()
    assert entry.name == "aws_credentials"
    assert entry.sensitive is True
    assert entry.proposed_by == "maintainer"  # default
    assert entry.schema_["type"] == "object"  # aliased "schema"


def test_value_type_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ValueTypeEntry.model_validate(
            {
                "name": "x",
                "description": "y",
                "schema": {},
                "sensitive": False,
                "bogus": True,
            }
        )
    assert "bogus" in str(exc.value)


def test_facet_entry_constructs_canonical_example() -> None:
    entry = _facet()
    assert entry.name == "target:aws"
    assert entry.category == "target"
    assert entry.first_class is False  # default


def test_facet_entry_rejects_empty_applies_at_levels() -> None:
    with pytest.raises(ValidationError):
        FacetEntry(
            name="target:aws",  # type: ignore[arg-type]
            category="target",
            proposed_by="extractor",
            description="x",
            applies_at_levels=[],
        )


def test_facet_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        FacetEntry.model_validate(
            {
                "name": "target:aws",
                "category": "target",
                "proposed_by": "extractor",
                "description": "x",
                "applies_at_levels": ["lab"],
                "bogus": True,
            }
        )
    assert "bogus" in str(exc.value)


def test_external_data_source_entry_constructs_canonical_example() -> None:
    entry = _external_data_source()
    assert entry.id == "nvd"
    assert entry.auth_env_var == "NVD_API_KEY"
    assert len(entry.enrichment_triggers) == 1
    assert entry.endpoints[0].cache_ttl == timedelta(days=7)


def test_external_data_source_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ExternalDataSourceEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "bogus_field": 1,
            }
        )
    assert "bogus_field" in str(exc.value)


def test_static_catalog_entry_constructs_canonical_example() -> None:
    entry = _static_catalog()
    assert entry.id == "aws_iam_catalog"
    assert entry.notes_for_generator is not None
    assert "lookup_cloud_iam_action" in entry.notes_for_generator


def test_execution_context_entry_constructs_canonical_example() -> None:
    entry = _execution_context()
    assert entry.name == "attacker_local"
    assert entry.proposed_by == "maintainer"


def test_execution_context_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ExecutionContextEntry.model_validate(
            {
                "name": "attacker_local",
                "description": "x",
                "credential_assumption": "x",
                "network_assumption": "x",
                "bogus": 1,
            }
        )
    assert "bogus" in str(exc.value)


def test_lab_credential_entry_constructs_canonical_example() -> None:
    entry = _lab_credential()
    assert entry.id == "aws_test_access_key"
    assert entry.platform == "aws"


def test_lab_credential_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        LabCredentialEntry.model_validate(
            {
                "id": "x",
                "platform": "aws",
                "description": "x",
                "pattern": "x",
                "example": "x",
                "whitelist_rationale": "x",
                "bogus": 1,
            }
        )
    assert "bogus" in str(exc.value)


# --- `_auth_rules` validator (inherited by both subclasses) ----------------


@pytest.mark.parametrize("auth_type", ["required_api_key", "optional_api_key"])
def test_auth_rules_external_data_source_requires_env_var(auth_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ExternalDataSourceEntry(
            id="x",
            name="x",
            description="x",
            base_url="https://example.com",  # type: ignore[arg-type]
            auth_type=auth_type,  # type: ignore[arg-type]
            rate_limit=_rate_limit(),
            cache=_cache(),
        )
    assert "auth_env_var required" in str(exc.value)


@pytest.mark.parametrize("auth_type", ["required_api_key", "optional_api_key"])
def test_auth_rules_static_catalog_requires_env_var(auth_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        StaticCatalogEntry(
            id="x",
            name="x",
            description="x",
            base_url="https://example.com",  # type: ignore[arg-type]
            auth_type=auth_type,  # type: ignore[arg-type]
            rate_limit=_rate_limit(),
            cache=_cache(),
        )
    assert "auth_env_var required" in str(exc.value)


@pytest.mark.parametrize("auth_type", ["none", "oauth"])
def test_auth_rules_no_env_var_required_for_none_or_oauth(auth_type: str) -> None:
    entry = ExternalDataSourceEntry(
        id="x",
        name="x",
        description="x",
        base_url="https://example.com",  # type: ignore[arg-type]
        auth_type=auth_type,  # type: ignore[arg-type]
        rate_limit=_rate_limit(),
        cache=_cache(),
    )
    assert entry.auth_env_var is None


def test_auth_rules_with_env_var_set_constructs() -> None:
    entry = ExternalDataSourceEntry(
        id="x",
        name="x",
        description="x",
        base_url="https://example.com",  # type: ignore[arg-type]
        auth_type="required_api_key",
        auth_env_var="API_KEY",
        rate_limit=_rate_limit(),
        cache=_cache(),
    )
    assert entry.auth_env_var == "API_KEY"


# --- Type-split discipline (schema-details.md §6.3) ------------------------


def test_static_catalog_rejects_enrichment_triggers() -> None:
    with pytest.raises(ValidationError) as exc:
        StaticCatalogEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "enrichment_triggers": [],
            }
        )
    assert "enrichment_triggers" in str(exc.value)


def test_static_catalog_rejects_discrepancy_materiality_rules() -> None:
    with pytest.raises(ValidationError) as exc:
        StaticCatalogEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "discrepancy_materiality_rules": [],
            }
        )
    assert "discrepancy_materiality_rules" in str(exc.value)


def test_static_catalog_rejects_notes_for_extractor() -> None:
    with pytest.raises(ValidationError) as exc:
        StaticCatalogEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "notes_for_extractor": "x",
            }
        )
    assert "notes_for_extractor" in str(exc.value)


def test_external_data_source_rejects_notes_for_generator() -> None:
    with pytest.raises(ValidationError) as exc:
        ExternalDataSourceEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "notes_for_generator": "x",
            }
        )
    assert "notes_for_generator" in str(exc.value)


# --- Inheritance-chain spot check (user hint #1) ---------------------------


def test_external_data_source_accepts_all_base_plus_subclass_fields() -> None:
    """Every base-class field plus every subclass-specific field constructs.

    Pins that ``_ExternalSourceEntryBase``'s ``extra='forbid'`` inheritance
    doesn't accidentally exclude legitimate subclass-additive fields.
    """
    entry = _external_data_source()
    base_fields = {
        "id",
        "name",
        "description",
        "base_url",
        "auth_type",
        "auth_env_var",
        "rate_limit",
        "endpoints",
        "cache",
        "best_effort",
    }
    subclass_fields = {
        "enrichment_triggers",
        "discrepancy_materiality_rules",
        "notes_for_extractor",
    }
    declared = set(type(entry).model_fields)
    assert base_fields.issubset(declared)
    assert subclass_fields.issubset(declared)


def test_external_data_source_rejects_foreign_field_belonging_to_neither() -> None:
    """A field that's neither base nor subclass-specific fails."""
    with pytest.raises(ValidationError) as exc:
        ExternalDataSourceEntry.model_validate(
            {
                "id": "x",
                "name": "x",
                "description": "x",
                "base_url": "https://example.com",
                "auth_type": "none",
                "rate_limit": {"without_key": "x"},
                "cache": {"ttl": "P1D", "scope": "global"},
                "totally_unrelated_field": "boom",
            }
        )
    assert "totally_unrelated_field" in str(exc.value)


# --- Per-registry containers ----------------------------------------------


@pytest.mark.parametrize(
    "registry_cls",
    [
        ValueTypesRegistry,
        FacetsRegistry,
        ExternalDataSourcesRegistry,
        StaticCatalogsRegistry,
        ExecutionContextsRegistry,
        LabCredentialsRegistry,
    ],
)
def test_registry_accepts_empty_entries(registry_cls: type[BaseModel]) -> None:
    instance = registry_cls.model_validate({"entries": []})
    # getattr bypasses pyright's BaseModel-doesn't-have-`entries` complaint;
    # the parametrize covers six concrete subclasses that all do.
    assert getattr(instance, "entries") == []  # noqa: B009


def test_value_types_registry_rejects_wrong_entry_shape() -> None:
    with pytest.raises(ValidationError):
        # FacetEntry-shaped dict inside ValueTypesRegistry should fail.
        ValueTypesRegistry.model_validate(
            {
                "entries": [
                    {
                        "name": "target:aws",
                        "category": "target",
                        "proposed_by": "extractor",
                        "description": "x",
                        "applies_at_levels": ["lab"],
                    }
                ]
            }
        )


# --- OverlayRegistryFile + _proposal_keys_match_entries --------------------


def test_overlay_empty_entries_and_empty_proposals_is_valid() -> None:
    overlay: OverlayRegistryFile[ValueTypeEntry] = OverlayRegistryFile[ValueTypeEntry]()
    assert overlay.entries == []
    assert overlay.proposals == {}


def test_overlay_entry_without_proposal_is_valid_maintainer_curated() -> None:
    overlay = OverlayRegistryFile[ValueTypeEntry](entries=[_value_type()])
    assert len(overlay.entries) == 1
    assert overlay.proposals == {}


def test_overlay_name_keyed_proposal_matches_entry() -> None:
    overlay = OverlayRegistryFile[ValueTypeEntry](
        entries=[_value_type("k8s_sa_token")],
        proposals={"k8s_sa_token": _proposal_audit()},  # type: ignore[dict-item]
    )
    assert "k8s_sa_token" in overlay.proposals


def test_overlay_id_keyed_proposal_matches_entry() -> None:
    overlay = OverlayRegistryFile[LabCredentialEntry](
        entries=[_lab_credential("aws_test_access_key")],
        proposals={"aws_test_access_key": _proposal_audit()},  # type: ignore[dict-item]
    )
    assert "aws_test_access_key" in overlay.proposals


def test_overlay_facet_keyed_proposal_matches_entry() -> None:
    """A facet proposal keyed by its ``category:value`` name is representable.

    Regression for ADR 0015: ``proposals`` was typed ``dict[SnakeName, ...]``,
    so a ``FacetName`` key (containing a colon) failed the pattern at parse
    time -- making facet proposals silently impossible. The key type is now
    ``RegistryKey`` (``SnakeName | FacetName``). This test fails on the old
    shape and passes on the fixed one.
    """
    overlay = OverlayRegistryFile[FacetEntry](
        entries=[_facet("target:azure")],
        proposals={"target:azure": _proposal_audit()},  # type: ignore[dict-item]
    )
    assert "target:azure" in overlay.proposals


def test_overlay_facet_orphan_proposal_key_fails() -> None:
    """The no-orphan-key guarantee still holds for facet-shaped keys.

    Widening the key type to ``RegistryKey`` must not loosen the rule that
    every proposal key corresponds to an entry. A facet key with no matching
    entry is still rejected.
    """
    with pytest.raises(ValidationError) as exc:
        OverlayRegistryFile[FacetEntry](
            entries=[_facet("target:aws")],
            proposals={"target:gcp": _proposal_audit()},  # type: ignore[dict-item]
        )
    assert "target:gcp" in str(exc.value)
    assert "no corresponding entry" in str(exc.value)


def test_overlay_facet_proposal_rejects_non_facet_non_snake_key() -> None:
    """A key matching neither ``SnakeName`` nor ``FacetName`` is still rejected.

    Confirms the union did not degrade to "any string": a key with a colon but
    an invalid category prefix is not a ``FacetName`` and not a ``SnakeName``,
    so it fails the key-type pattern before the orphan check even runs.
    """
    with pytest.raises(ValidationError):
        OverlayRegistryFile[FacetEntry].model_validate(
            {
                "entries": [],
                "proposals": {"bogus:value": _proposal_audit().model_dump(mode="json")},
            }
        )


def test_overlay_orphan_proposal_key_fails() -> None:
    with pytest.raises(ValidationError) as exc:
        OverlayRegistryFile[ValueTypeEntry](
            entries=[_value_type("foo")],
            proposals={"orphan_key": _proposal_audit()},  # type: ignore[dict-item]
        )
    assert "orphan_key" in str(exc.value)
    assert "no corresponding entry" in str(exc.value)


# --- BundledRegistryFile ---------------------------------------------------


def test_bundled_empty_entries_is_valid() -> None:
    bundled: BundledRegistryFile[ValueTypeEntry] = BundledRegistryFile[ValueTypeEntry]()
    assert bundled.entries == []


def test_bundled_rejects_proposals_block() -> None:
    """A bundled file accidentally carrying overlay-only audit fails Layer 1."""
    with pytest.raises(ValidationError) as exc:
        BundledRegistryFile[ValueTypeEntry].model_validate({"entries": [], "proposals": {}})
    assert "proposals" in str(exc.value)


# --- ProposalAuditBlock ----------------------------------------------------


def test_proposal_audit_block_constructs() -> None:
    block = _proposal_audit()
    assert block.proposed_by_model == "claude-opus-4-7"
    assert block.proposal_origin == "llm_during_extraction"


def test_proposal_audit_block_rejects_invalid_origin() -> None:
    with pytest.raises(ValidationError):
        ProposalAuditBlock.model_validate(
            {
                "proposal_origin": "manually_typed_by_user",
                "source_lab": "x",
                "source_blog": "https://example.com",
                "proposed_by_model": "x",
                "proposed_at": "2026-05-18T00:00:00Z",
                "reasoning": "x",
            }
        )


# --- YAML round-trip for an entry (covers the schema_/schema alias) --------


def test_value_type_entry_yaml_round_trip_preserves_schema_alias() -> None:
    """Round-trip uses the YAML-friendly alias `schema:` rather than `schema_:`.

    Confirms ``ArtifactModel.to_yaml`` passes ``by_alias=True`` so a registry
    YAML file an agent emits matches what a human-written file would use.
    """
    original = _value_type()
    yaml_text = original.to_yaml()
    assert "schema:" in yaml_text
    assert "schema_:" not in yaml_text
    parsed = ValueTypeEntry.from_yaml(yaml_text)
    assert parsed == original


def test_overlay_file_yaml_round_trip() -> None:
    original = OverlayRegistryFile[ValueTypeEntry](
        entries=[_value_type("k8s_sa_token")],
        proposals={"k8s_sa_token": _proposal_audit()},  # type: ignore[dict-item]
    )
    yaml_text = original.to_yaml()
    # to_yaml on a generic instance dumps to dicts; parse back via the
    # parameterized subclass.
    parsed = OverlayRegistryFile[ValueTypeEntry].from_yaml(yaml_text)
    assert parsed.entries == original.entries
    assert set(parsed.proposals) == set(original.proposals)


# --- Smoke test: ENTRY_KEY_FIELD declared on every entry type --------------


@pytest.mark.parametrize(
    "entry_cls,expected_key",
    [
        (ValueTypeEntry, "name"),
        (FacetEntry, "name"),
        (ExecutionContextEntry, "name"),
        (ExternalDataSourceEntry, "id"),
        (StaticCatalogEntry, "id"),
        (LabCredentialEntry, "id"),
    ],
)
def test_entry_key_field_class_var_is_declared(entry_cls: type[Any], expected_key: str) -> None:
    """Every entry type declares its registry key via ``ENTRY_KEY_FIELD``.

    Pins the convention the ``OverlayRegistryFile._entry_key`` resolver
    depends on; a missing declaration would break overlay proposal-key
    matching for that registry.
    """
    # getattr is the right tool here -- the parametrize iterates entry classes
    # of different concrete types, so direct attribute access would force a
    # cast per row.
    assert getattr(entry_cls, "ENTRY_KEY_FIELD") == expected_key  # noqa: B009
