"""Merge bundled + overlay registries into the runtime ``MergedRegistries`` view.

``schema-details.md §6.6`` and ``schema.md §4.11``. Overlay wins on
key collisions, where the key is each entry's ``ENTRY_KEY_FIELD``
(``name`` for most types, ``id`` for external-source and lab-credential
entries). The merge happens at the per-registry list level; the merged
result is a fully-typed ``MergedRegistries`` with O(1) accessors backed
by ``PrivateAttr`` indices built in ``model_validator(mode='after')``.

Base-class choice: ``BaseModel`` under ADR 0004 reserved case 3 (ad-hoc
internal type that never serializes). ``MergedRegistries`` is a runtime
view, never round-trips through YAML, never crosses the artifact
boundary. ``frozen=True`` enforces the immutability that ``architecture.md
§1.5`` requires of shared state (LLMs never modify shared state outside
their designated output). ``extra='forbid'`` catches field-name typos at
construction. ``arbitrary_types_allowed=True`` from the doc is dropped:
every field is already a Pydantic ``ArtifactModel`` subtype. ADR 0008
records the rationale.
"""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator

from cyberlab_gen.registries.loader import (
    LoadedRegistryLayer,
    load_bundled,
    load_overlay,
)
from cyberlab_gen.schemas.registries import (
    ExecutionContextEntry,
    ExecutionContextsRegistry,
    ExternalDataSourceEntry,
    ExternalDataSourcesRegistry,
    FacetEntry,
    FacetsRegistry,
    LabCredentialEntry,
    LabCredentialsRegistry,
    StaticCatalogEntry,
    StaticCatalogsRegistry,
    ThesisTypeEntry,
    ThesisTypesRegistry,
    ValueTypeEntry,
    ValueTypesRegistry,
)


def _merge_entries[E: BaseModel](bundled: list[E], overlay: list[E]) -> list[E]:
    """Overlay-wins merge keyed by each entry's ``ENTRY_KEY_FIELD``.

    Insertion order: bundled entries first (in their original order),
    then overlay entries that introduce new keys. An overlay entry whose
    key matches a bundled entry replaces the bundled entry in-place
    (preserving the bundled position) rather than appending to the tail.
    """
    if not bundled and not overlay:
        return []
    # Pick the key field from the first entry's class; every entry in a
    # given registry shares the same concrete type and therefore the
    # same ``ENTRY_KEY_FIELD``.
    sample = bundled[0] if bundled else overlay[0]
    # ``ENTRY_KEY_FIELD`` is a ``ClassVar`` on each concrete entry subclass;
    # the static type here is ``BaseModel``, so ``getattr`` is the right
    # read tool (matching ``cyberlab_gen/schemas/registries.py``).
    key_field: str = getattr(type(sample), "ENTRY_KEY_FIELD")  # noqa: B009

    by_key: dict[object, E] = {getattr(e, key_field): e for e in bundled}
    order: list[object] = [getattr(e, key_field) for e in bundled]
    for entry in overlay:
        k: object = getattr(entry, key_field)
        if k not in by_key:
            order.append(k)
        by_key[k] = entry
    return [by_key[k] for k in order]


class MergedRegistries(BaseModel):
    """Runtime view of all six registries with bundled + overlay merged.

    Built once per CLI invocation (or test fixture) and passed read-only
    to every downstream stage. ``frozen=True`` blocks mid-run mutation;
    private dict indices are populated in ``_build_indices`` and provide
    O(1) lookup behind the accessor methods. Accessor signatures match
    ``schema-details.md §6.6``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value_types: ValueTypesRegistry
    facets: FacetsRegistry
    external_data_sources: ExternalDataSourcesRegistry
    static_catalogs: StaticCatalogsRegistry
    execution_contexts: ExecutionContextsRegistry
    lab_credentials: LabCredentialsRegistry
    thesis_types: ThesisTypesRegistry

    _value_type_index: dict[str, ValueTypeEntry] = PrivateAttr(default_factory=dict)
    _facet_index: dict[str, FacetEntry] = PrivateAttr(default_factory=dict)
    _external_source_index: dict[str, ExternalDataSourceEntry] = PrivateAttr(default_factory=dict)
    _static_catalog_index: dict[str, StaticCatalogEntry] = PrivateAttr(default_factory=dict)
    _execution_context_index: dict[str, ExecutionContextEntry] = PrivateAttr(default_factory=dict)
    _thesis_type_index: dict[str, ThesisTypeEntry] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _build_indices(self) -> Self:
        self._value_type_index = {e.name: e for e in self.value_types.entries}
        self._facet_index = {e.name: e for e in self.facets.entries}
        self._external_source_index = {e.id: e for e in self.external_data_sources.entries}
        self._static_catalog_index = {e.id: e for e in self.static_catalogs.entries}
        self._execution_context_index = {e.name: e for e in self.execution_contexts.entries}
        self._thesis_type_index = {e.name: e for e in self.thesis_types.entries}
        # lab_credentials uses a list-with-filter accessor rather than a
        # single-key dict; the safety-scan pass scans the catalog and a per-platform
        # bucket would be premature for ~7 v1 entries.
        return self

    def value_type(self, name: str) -> ValueTypeEntry | None:
        return self._value_type_index.get(name)

    def facet(self, name: str) -> FacetEntry | None:
        return self._facet_index.get(name)

    def external_source(self, source_id: str) -> ExternalDataSourceEntry | None:
        return self._external_source_index.get(source_id)

    def static_catalog(self, catalog_id: str) -> StaticCatalogEntry | None:
        return self._static_catalog_index.get(catalog_id)

    def execution_context(self, name: str) -> ExecutionContextEntry | None:
        return self._execution_context_index.get(name)

    def thesis_type(self, name: str) -> ThesisTypeEntry | None:
        return self._thesis_type_index.get(name)

    def lab_credential_patterns(self, platform: str | None = None) -> list[LabCredentialEntry]:
        """Return lab-credential patterns, optionally filtered by ``platform``.

        ``platform=None`` returns every entry — the default scan
        the safety-scan pass runs against generated content. A specific
        platform returns only entries whose ``platform`` field matches;
        unknown platforms return an empty list.
        """
        if platform is None:
            return list(self.lab_credentials.entries)
        return [e for e in self.lab_credentials.entries if e.platform == platform]


def merge_layers(bundled: LoadedRegistryLayer, overlay: LoadedRegistryLayer) -> MergedRegistries:
    """Merge a bundled layer and an overlay layer into ``MergedRegistries``.

    Overlay wins on key collisions per ``schema.md §4.11``.
    """
    return MergedRegistries(
        value_types=ValueTypesRegistry(
            entries=_merge_entries(
                bundled.value_types_file.entries, overlay.value_types_file.entries
            )
        ),
        facets=FacetsRegistry(
            entries=_merge_entries(bundled.facets_file.entries, overlay.facets_file.entries)
        ),
        external_data_sources=ExternalDataSourcesRegistry(
            entries=_merge_entries(
                bundled.external_data_sources_file.entries,
                overlay.external_data_sources_file.entries,
            )
        ),
        static_catalogs=StaticCatalogsRegistry(
            entries=_merge_entries(
                bundled.static_catalogs_file.entries,
                overlay.static_catalogs_file.entries,
            )
        ),
        execution_contexts=ExecutionContextsRegistry(
            entries=_merge_entries(
                bundled.execution_contexts_file.entries,
                overlay.execution_contexts_file.entries,
            )
        ),
        lab_credentials=LabCredentialsRegistry(
            entries=_merge_entries(
                bundled.lab_credentials_file.entries,
                overlay.lab_credentials_file.entries,
            )
        ),
        thesis_types=ThesisTypesRegistry(
            entries=_merge_entries(
                bundled.thesis_types_file.entries,
                overlay.thesis_types_file.entries,
            )
        ),
    )


def load_merged_registries(overlay_dir: Path | None = None) -> MergedRegistries:
    """Top-level convenience: load both layers and return the merged view."""
    bundled = load_bundled()
    overlay = load_overlay(overlay_dir)
    return merge_layers(bundled, overlay)


def reload_merged_registries(overlay_dir: Path | None = None) -> MergedRegistries:
    """Re-read the merged registries after an overlay write — the stale-snapshot invalidation seam.

    ``MergedRegistries`` is ``frozen`` (``architecture.md §1.5`` — shared state is immutable), so a
    proposal accepted to the overlay does NOT mutate the in-process snapshot. A second proposer in the
    same process (the Planner after the Extractor, in ``generate``) must therefore re-read to see
    just-accepted entries (``dev/phase-2-seams.md §2``; ADR 0099). This is that re-read: it reloads
    bundled + overlay from disk and returns a fresh, immutable view. Currently the only sound caller is
    the future two-proposer flow (Planner promotion is Task 8; ``generate`` is Phase 3) — it is named
    here so that wiring re-reads at one documented seam rather than re-deriving it. Identical in
    behaviour to :func:`load_merged_registries`; the distinct name records the *intent* (invalidate,
    not first-load).
    """
    return load_merged_registries(overlay_dir)
