"""Pydantic meta-schemas for every registry's entry shape.

Architectural source: ``schema-details.md`` §6 (registry meta-schemas);
``schema.md`` §4.11 (registry hierarchy), §4.14 (external_data_sources vs
static_catalogs semantic split), §4.16 (proposal lifecycle).

Per ADR 0004 every class here extends ``ArtifactModel`` rather than the
``BaseModel + ConfigDict(extra='forbid')`` pattern shown in §6 — that
keeps the five load-bearing settings (``validate_assignment``,
``str_strip_whitespace``, ``use_enum_values=False``, ``populate_by_name``,
and ``extra='forbid'`` itself) inherited rather than re-declared per class.
``_ExternalSourceEntryBase`` is included even though it never serializes
directly; its config propagates to the two serializing subclasses.

The type-split discipline (``schema-details.md §6.3``) is purely structural:
``StaticCatalogEntry`` rejects ``enrichment_triggers`` /
``discrepancy_materiality_rules`` / ``notes_for_extractor`` via the
inherited ``extra='forbid'``, and ``ExternalDataSourceEntry`` rejects
``notes_for_generator`` the same way. No separate registry-level validators
are needed; the test suite pins the guarantee explicitly.

``ENTRY_KEY_FIELD`` is a ``ClassVar[str]`` on each entry type that tells
``OverlayRegistryFile._proposal_keys_match_entries`` which field is the
registry key. Cleaner than the doc's ``getattr(entry, 'name', None) or
getattr(entry, 'id')`` chain — explicit per class, no quiet fallback.
"""

from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field, model_validator

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.primitives import (
    FacetName,
    HttpUrl,
    NonEmptyString,
    SnakeName,
)

# --- Supporting types for external-source entries --------------------------


class ExternalSourceParam(ArtifactModel):
    """One parameter declaration for an external-source endpoint.

    ``schema-details.md`` §6.3.
    """

    type: Literal["string", "integer", "boolean", "list_string"]
    required: bool = True
    description: NonEmptyString | None = None
    pattern: NonEmptyString | None = None
    enum_values: list[NonEmptyString] | None = None


class ExternalSourceEndpoint(ArtifactModel):
    """One operation an external-source adapter exposes.

    ``schema-details.md`` §6.3. ``response_schema_ref`` is a ``SnakeName``
    reference resolved at runtime by the adapter module under
    ``cyberlab_gen/external_data_sources/<source_id>/`` (§6.7); registry
    validation here only checks well-formedness.
    """

    id: SnakeName
    method: Literal["GET", "POST"]
    path_template: NonEmptyString
    parameters: dict[str, ExternalSourceParam] = Field(
        default_factory=dict[str, ExternalSourceParam]
    )
    response_schema_ref: SnakeName | None = None
    cache_ttl: timedelta


class RateLimit(ArtifactModel):
    """Human-readable rate-limit declarations. ``schema-details.md`` §6.3."""

    without_key: NonEmptyString | None = None
    with_key: NonEmptyString | None = None


class CacheConfig(ArtifactModel):
    """Cache policy for an external-source adapter. ``schema-details.md`` §6.3."""

    ttl: timedelta
    scope: Literal["per-key", "global"] = "per-key"


class EnrichmentTrigger(ArtifactModel):
    """Declaration of when the framework auto-calls an external-source endpoint.

    Only meaningful on ``ExternalDataSourceEntry`` (per ``schema.md §4.14``);
    rejected on ``StaticCatalogEntry`` via the inherited ``extra='forbid'``.
    """

    field: NonEmptyString
    action: Literal["lookup"]
    endpoint: SnakeName


class DiscrepancyMaterialityRule(ArtifactModel):
    """Per-field rule classifying API-vs-blog discrepancies.

    Only on ``ExternalDataSourceEntry``. Read by the pre-Planner enrichment
    layer (``pipeline.md §3.2.4``). ``schema.md §4.14``.
    """

    field_path: NonEmptyString
    classification: Literal["material", "non_material"]
    rule_description: NonEmptyString


# --- External-source entry split (the §6.3 type-split discipline) ----------


class _ExternalSourceEntryBase(ArtifactModel):
    """Shared shape for the two external-source entry types.

    Not instantiated directly — split into ``ExternalDataSourceEntry`` and
    ``StaticCatalogEntry`` because the two registries have different
    semantic roles (``schema.md §4.14``). ``ExternalDataSourceEntry`` adds
    the enrichment-only fields; ``StaticCatalogEntry`` adds
    ``notes_for_generator``. Both inherit ``extra='forbid'``, which makes
    the type split structural: a YAML entry under ``static_catalogs:`` with
    an ``enrichment_triggers`` field fails Layer 1.

    Carries the ``_auth_rules`` validator so both subclasses inherit it.
    """

    ENTRY_KEY_FIELD: ClassVar[str] = "id"

    id: SnakeName
    name: NonEmptyString
    description: NonEmptyString
    base_url: HttpUrl
    auth_type: Literal["none", "optional_api_key", "required_api_key", "oauth"]
    auth_env_var: NonEmptyString | None = None
    rate_limit: RateLimit
    endpoints: list[ExternalSourceEndpoint] = Field(default_factory=list[ExternalSourceEndpoint])
    cache: CacheConfig
    best_effort: bool = False

    @model_validator(mode="after")
    def _auth_rules(self) -> Self:
        if self.auth_type in ("required_api_key", "optional_api_key") and not self.auth_env_var:
            raise ValueError(f"auth_env_var required when auth_type is {self.auth_type}")
        return self


class ExternalDataSourceEntry(_ExternalSourceEntryBase):
    """Entry in the ``external_data_sources`` registry.

    Called automatically during pre-Planner enrichment. Carries
    ``enrichment_triggers``, ``discrepancy_materiality_rules``, and
    ``notes_for_extractor``. ``schema.md §4.14``.
    """

    enrichment_triggers: list[EnrichmentTrigger] = Field(default_factory=list[EnrichmentTrigger])
    discrepancy_materiality_rules: list[DiscrepancyMaterialityRule] = Field(
        default_factory=list[DiscrepancyMaterialityRule]
    )
    notes_for_extractor: NonEmptyString | None = None


class StaticCatalogEntry(_ExternalSourceEntryBase):
    """Entry in the ``static_catalogs`` registry.

    Consulted on-demand by the Generator and Validator (e.g.,
    ``lookup_cloud_iam_action``); never enrichment-triggered. Carries only
    ``notes_for_generator`` beyond the base. ``schema.md §4.14``.
    """

    notes_for_generator: NonEmptyString | None = None


# --- The other four registry entry types -----------------------------------


class ValueTypeEntry(ArtifactModel):
    """Entry in the ``value_types`` registry. ``schema.md §4.12``.

    ``schema_`` carries the JSON Schema for the value's shape; aliased to
    ``schema`` so YAML reads naturally (``schema:`` in the file).
    ``populate_by_name=True`` on ``ArtifactModel`` accepts both names on
    parse; ``to_yaml`` uses ``by_alias=True`` so output matches input.
    """

    ENTRY_KEY_FIELD: ClassVar[str] = "name"

    name: SnakeName
    description: NonEmptyString
    # JSON Schema is intentionally open-shape (the value-type catalog covers
    # every shape the system might ever encounter); narrower typing would
    # exclude valid registrations.
    schema_: dict[str, Any] = Field(alias="schema")
    sensitive: bool
    # Example values mirror the value's runtime shape, which is open-set by
    # the same argument.
    examples: list[Any] = Field(default_factory=list[Any])
    notes_for_generator: NonEmptyString | None = None
    cleanup_metadata: NonEmptyString | None = None
    platforms: list[SnakeName] = Field(default_factory=list[SnakeName])
    proposed_by: Literal["extractor", "planner", "maintainer"] = "maintainer"
    proposed_in_run: str | None = None


class FacetEntry(ArtifactModel):
    """Entry in the ``facets`` registry. ``schema.md §4.13``."""

    ENTRY_KEY_FIELD: ClassVar[str] = "name"

    name: FacetName
    category: Literal["target", "runtime", "lab_class_signal"]
    proposed_by: Literal["extractor", "planner", "maintainer"]
    description: NonEmptyString
    applies_at_levels: list[Literal["lab", "phase", "step"]] = Field(min_length=1)
    requires_fields: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    implies: list[FacetName] = Field(default_factory=list[FacetName])
    incompatible_with: list[FacetName] = Field(default_factory=list[FacetName])
    examples: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    first_class: bool = False
    notes_for_extractor: NonEmptyString | None = None
    notes_for_planner: NonEmptyString | None = None


class ExecutionContextEntry(ArtifactModel):
    """Entry in the ``execution_contexts`` registry. ``schema.md §4.5``."""

    ENTRY_KEY_FIELD: ClassVar[str] = "name"

    name: SnakeName
    description: NonEmptyString
    credential_assumption: NonEmptyString
    network_assumption: NonEmptyString
    notes_for_generator: NonEmptyString | None = None
    typical_use_cases: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    proposed_by: Literal["planner", "maintainer"] = "maintainer"


class LabCredentialEntry(ArtifactModel):
    """Entry in the ``lab_credentials`` registry.

    Canonical fake-credential patterns the Generator may plant and
    Validator Layer 5 whitelists. ``schema.md §4.11``,
    ``validation.md §6.8``.
    """

    ENTRY_KEY_FIELD: ClassVar[str] = "id"

    id: SnakeName
    platform: SnakeName
    description: NonEmptyString
    pattern: NonEmptyString
    example: NonEmptyString
    whitelist_rationale: NonEmptyString


# --- Per-registry containers (`{entries: [...]}` shape) --------------------


class ValueTypesRegistry(ArtifactModel):
    """The ``value_types`` registry as a list of entries."""

    entries: list[ValueTypeEntry] = Field(default_factory=list[ValueTypeEntry])


class FacetsRegistry(ArtifactModel):
    """The ``facets`` registry as a list of entries."""

    entries: list[FacetEntry] = Field(default_factory=list[FacetEntry])


class ExternalDataSourcesRegistry(ArtifactModel):
    """The ``external_data_sources`` registry as a list of entries."""

    entries: list[ExternalDataSourceEntry] = Field(default_factory=list[ExternalDataSourceEntry])


class StaticCatalogsRegistry(ArtifactModel):
    """The ``static_catalogs`` registry as a list of entries."""

    entries: list[StaticCatalogEntry] = Field(default_factory=list[StaticCatalogEntry])


class ExecutionContextsRegistry(ArtifactModel):
    """The ``execution_contexts`` registry as a list of entries."""

    entries: list[ExecutionContextEntry] = Field(default_factory=list[ExecutionContextEntry])


class LabCredentialsRegistry(ArtifactModel):
    """The ``lab_credentials`` registry as a list of entries."""

    entries: list[LabCredentialEntry] = Field(default_factory=list[LabCredentialEntry])


# --- Per-file shapes (bundled vs overlay) ----------------------------------


class ProposalAuditBlock(ArtifactModel):
    """Audit envelope for an overlay-resident proposal.

    Captures the metadata the framework recorded at proposal time
    (``schema.md §4.16``). Lives in the overlay file's ``proposals:`` map
    keyed by entry name; dropped when the entry is promoted to bundled
    (history is preserved in git). Not part of the registry-entry shape.
    """

    proposal_origin: Literal["llm_during_extraction", "llm_during_planning"]
    source_lab: NonEmptyString
    source_blog: HttpUrl
    proposed_by_model: NonEmptyString
    proposed_at: datetime
    reasoning: NonEmptyString


class OverlayRegistryFile[E: BaseModel](ArtifactModel):
    """Shape of an overlay registry YAML file.

    Generic over the registry's entry type. ``proposals`` is keyed by
    entry registry-key (``name`` for most types, ``id`` for external-source
    and lab-credential entries); every key must correspond to an entry in
    ``entries``. Entries without a proposal key are maintainer-curated
    overlay additions, which are rare but valid.

    The generic bound is ``BaseModel`` rather than a tighter
    ``RegistryEntry``-style mixin per ADR 0004 reserved case 2.
    """

    entries: list[E] = Field(default_factory=list[E])
    proposals: dict[SnakeName, ProposalAuditBlock] = Field(
        default_factory=dict[SnakeName, ProposalAuditBlock]
    )

    @model_validator(mode="after")
    def _proposal_keys_match_entries(self) -> Self:
        entry_keys = {self._entry_key(e) for e in self.entries}
        for name in self.proposals:
            if name not in entry_keys:
                raise ValueError(f"proposals[{name!r}] has no corresponding entry in `entries`")
        return self

    @staticmethod
    def _entry_key(entry: BaseModel) -> str:
        """Resolve the registry key for an entry via its ``ENTRY_KEY_FIELD``.

        Each entry class declares ``ENTRY_KEY_FIELD: ClassVar[str]``; this
        beats the doc's ``getattr(entry, 'name', None) or getattr(entry,
        'id')`` chain because a missing declaration surfaces as a clear
        ``AttributeError`` instead of silently falling back.
        """
        # The entry's static type is BaseModel (the generic bound); the
        # ENTRY_KEY_FIELD attribute lives on the concrete subclass. getattr
        # is the right tool to read it without forcing every caller to cast.
        key_field: str = getattr(type(entry), "ENTRY_KEY_FIELD")  # noqa: B009
        return getattr(entry, key_field)


class BundledRegistryFile[E: BaseModel](ArtifactModel):
    """Shape of a bundled registry YAML file.

    Only an ``entries:`` block — bundled files never carry proposal audit
    context (it lives in git history once promoted). A bundled file that
    accidentally includes a ``proposals:`` block fails Layer 1 via the
    inherited ``extra='forbid'``.
    """

    entries: list[E] = Field(default_factory=list[E])
