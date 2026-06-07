"""Validator Layer 1 — static schema validation + registry reference resolution.

Architectural source: ``validation.md §6.4`` (what Layer 1 checks),
``validation.md §6.10`` (failure routing — Layer 1 → the responsible agent's
*retry* mechanism, never refinement), ADR 0022 (this module's location).

Layer 1 is the cheapest, highest-coverage mechanical layer. It runs deterministic
checks — **no LLM, no network** (``validation.md §6.1``, ``architecture.md
§1.6``). For Phase 1 (AttackSpec only; the LabManifest path lands in Phase 2) it
checks, in order:

1. **Static schema validation** — the AttackSpec round-trips through its own
   Pydantic model (``model_validate(model_dump())``). Pydantic *is* the JSON
   Schema validator here (``schema-details.md §1``: ``extra="forbid"`` makes
   unknown fields a Layer-1 error). A spec handed in already-typed is structurally
   valid by construction, but a spec that arrived from a user edit or a refinement
   re-run is re-validated so a smuggled-in bad value surfaces as a finding rather
   than a crash later.
2. **``spec_kind`` discriminator** — the spec's ``spec_kind`` must be
   ``attack_spec`` at the AttackSpec loading point. Loading a Manifest where an
   AttackSpec is expected fails loudly (``validation.md §6.4``).
3. **Registry reference resolution** — every registry/catalog reference in the
   spec resolves against the merged registry (bundled + overlay) and the closed
   bundled-only catalogs (ADR 0016): facets (``FacetName`` → merged ``facets``
   registry), thesis types (``ThesisType`` → merged ``thesis_types`` registry —
   runtime-proposable since ADR 0045), CVE/advisory ``source_of_record`` /
   ``source`` (→ merged ``external_data_sources`` registry). The closed *enums*
   (``Severity``, ``DetectionComponent``,
   ``DetectionFormat``, ``ProvisioningMechanism``) are already validated by
   Pydantic at construction, so Layer 1 does not re-check them — but it confirms
   each appears in its closed catalog so a catalog/enum drift is caught.

The validator **never mutates** the spec and **never routes**: it returns a
``StaticSchemaResult`` of findings, and the orchestrator
(``cyberlab_gen.framework.orchestrator``) reads it and decides what to do
(``architecture.md §1.5``). A failing result routes back to the Extractor's
retry, per ``validation.md §6.10``.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.enums import (
    DetectionComponent,
    DetectionFormat,
    ProvisioningMechanism,
    Severity,
    SpecKind,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import DetectionFormatEntry
    from cyberlab_gen.schemas.catalogs import (
        DetectionComponentsCatalog,
        DetectionFormatsCatalog,
        ProvisioningMechanismsCatalog,
        SeverityLevelsCatalog,
    )

logger = logging.getLogger(__name__)


class PendingProposals(InternalModel):
    """In-flight registry proposals this run, for provisional reference resolution.

    A reference that is absent from the merged registry / closed catalog but whose
    name appears here is a **provisional pass** (logged, not a finding): the
    Extractor proposed it this run and the framework will write it to the overlay at
    the acceptance point (ADR 0044), so the term becomes durably resolvable next run
    and for ``cyberlab-gen validate``. Empty by default — with no proposals Layer 1
    resolves strictly, exactly as before. ``value_types`` / ``execution_contexts``
    are carried for forward-compatibility; Phase-1 Layer 1 has no reference check for
    them yet, so populating them is currently a no-op.
    """

    facets: frozenset[str] = frozenset()
    thesis_types: frozenset[str] = frozenset()
    value_types: frozenset[str] = frozenset()
    execution_contexts: frozenset[str] = frozenset()


class StaticSchemaCode(StrEnum):
    """The kinds of structural violation Layer 1 can report (``validation.md §6.4``)."""

    SCHEMA_INVALID = "schema_invalid"
    SPEC_KIND_MISMATCH = "spec_kind_mismatch"
    UNKNOWN_FACET = "unknown_facet"
    UNKNOWN_THESIS_TYPE = "unknown_thesis_type"
    UNKNOWN_EXTERNAL_SOURCE = "unknown_external_source"
    CATALOG_DRIFT = "catalog_drift"


class StaticSchemaFinding(InternalModel):
    """One Layer-1 violation: a code, a field locator, and a human-readable detail.

    ``location`` uses the JSONPath-like convention shared with ``GapEntry`` so a
    retry can target the offending field. ``InternalModel`` because the finding is
    consumed in-process by the orchestrator and surfaced in the run report; it is
    not an artifact.
    """

    code: StaticSchemaCode
    location: str
    detail: str

    def render(self) -> str:
        """A one-line ``code@location: detail`` rendering for logs / the report."""
        return f"{self.code.value}@{self.location}: {self.detail}"


class StaticSchemaResult(InternalModel):
    """The Layer-1 verdict: a pass/fail plus the findings list (``validation.md §6.9``)."""

    passed: bool
    findings: list[StaticSchemaFinding] = Field(default_factory=list[StaticSchemaFinding])

    def rendered_findings(self) -> list[str]:
        """Every finding rendered as a one-line string (for ``ValidationError``)."""
        return [f.render() for f in self.findings]


class StaticSchemaValidator:
    """Runs Validator Layer 1 over an ``AttackSpec`` (``validation.md §6.4``).

    Constructed with the merged registries (bundled + overlay) and, optionally,
    the closed bundled-only catalogs; the catalogs default to the bundled ones
    loaded on demand. The validator is stateless across calls and never mutates
    its inputs.
    """

    def __init__(
        self,
        *,
        registries: MergedRegistries,
        severity_levels: SeverityLevelsCatalog | None = None,
        detection_components: DetectionComponentsCatalog | None = None,
        detection_formats: DetectionFormatsCatalog | None = None,
        provisioning_mechanisms: ProvisioningMechanismsCatalog | None = None,
    ) -> None:
        self._registries = registries
        self._severity_levels = severity_levels
        self._detection_components = detection_components
        self._detection_formats = detection_formats
        self._provisioning_mechanisms = provisioning_mechanisms

    # --- public surface ----------------------------------------------------

    def validate(
        self, spec: AttackSpec, *, pending: PendingProposals | None = None
    ) -> StaticSchemaResult:
        """Validate ``spec`` and return a ``StaticSchemaResult``.

        Runs the three Layer-1 checks in cost order: schema (cheapest, and a
        schema failure short-circuits the rest because reference resolution would
        read malformed data), then the ``spec_kind`` discriminator, then registry
        reference resolution. Never raises on a *structural* problem — those are
        findings; the orchestrator decides routing.

        ``pending`` carries the run's in-flight registry proposals: a reference
        absent from the registry but named in ``pending`` is a provisional pass
        (logged, not a finding) so the proposal survives to the overlay-write
        acceptance point (ADR 0044). ``None`` resolves strictly.
        """
        pending = pending or PendingProposals()
        schema_findings = self._check_schema(spec)
        if schema_findings:
            return StaticSchemaResult(passed=False, findings=schema_findings)

        findings: list[StaticSchemaFinding] = []
        findings.extend(self._check_spec_kind(spec))
        findings.extend(self._check_facets(spec, pending))
        findings.extend(self._check_thesis_types(spec, pending))
        findings.extend(self._check_external_sources(spec))
        findings.extend(self._check_closed_catalog_membership(spec))

        passed = not findings
        if not passed:
            logger.info("layer 1 failed with %d finding(s)", len(findings))
        return StaticSchemaResult(passed=passed, findings=findings)

    # --- check 1: static schema -------------------------------------------

    def _check_schema(self, spec: AttackSpec) -> list[StaticSchemaFinding]:
        """Re-validate the spec against its own model (the JSON-Schema check)."""
        try:
            AttackSpec.model_validate(spec.model_dump(mode="json", by_alias=True))
        except PydanticValidationError as exc:
            return [
                StaticSchemaFinding(
                    code=StaticSchemaCode.SCHEMA_INVALID,
                    location=".".join(str(p) for p in err["loc"]) or "<root>",
                    detail=err["msg"],
                )
                for err in exc.errors()
            ]
        return []

    # --- check 2: spec_kind discriminator ---------------------------------

    def _check_spec_kind(self, spec: AttackSpec) -> list[StaticSchemaFinding]:
        """The spec must declare ``spec_kind == attack_spec`` at this load point."""
        if spec.spec_kind is not SpecKind.ATTACK_SPEC:
            return [
                StaticSchemaFinding(
                    code=StaticSchemaCode.SPEC_KIND_MISMATCH,
                    location="spec_kind",
                    detail=(
                        f"expected spec_kind={SpecKind.ATTACK_SPEC.value!r} at the AttackSpec "
                        f"load point, got {spec.spec_kind.value!r}"
                    ),
                )
            ]
        return []

    # --- check 3: registry reference resolution ---------------------------

    def _check_facets(
        self, spec: AttackSpec, pending: PendingProposals
    ) -> list[StaticSchemaFinding]:
        """Every declared facet must resolve in the merged ``facets`` registry.

        A facet absent from the registry but carrying an in-flight proposal this run
        is a provisional pass (ADR 0044): logged, not a finding.
        """
        findings: list[StaticSchemaFinding] = []
        for i, facet in enumerate(spec.facets):
            if self._registries.facet(facet) is None:
                if facet in pending.facets:
                    logger.info("facet %r provisionally resolved by an in-flight proposal", facet)
                    continue
                findings.append(
                    StaticSchemaFinding(
                        code=StaticSchemaCode.UNKNOWN_FACET,
                        location=f"facets[{i}]",
                        detail=(
                            f"facet {facet!r} does not resolve in the merged facets registry "
                            "(bundled + overlay)"
                        ),
                    )
                )
        return findings

    def _check_thesis_types(
        self, spec: AttackSpec, pending: PendingProposals
    ) -> list[StaticSchemaFinding]:
        """Every thesis type must resolve in the merged ``thesis_types`` registry.

        ADR 0045: ``thesis_types`` is a runtime-proposable registry (bundled +
        overlay), no longer a closed bundled-only catalog (reversing ADR 0016). A
        thesis type carrying an in-flight proposal this run is a provisional pass
        (ADR 0044): logged, not a finding.
        """
        if spec.thesis is None:
            return []
        findings: list[StaticSchemaFinding] = []
        for i, thesis_type in enumerate(spec.thesis.types):
            if self._registries.thesis_type(thesis_type) is None:
                if thesis_type in pending.thesis_types:
                    logger.info(
                        "thesis type %r provisionally resolved by an in-flight proposal",
                        thesis_type,
                    )
                    continue
                findings.append(
                    StaticSchemaFinding(
                        code=StaticSchemaCode.UNKNOWN_THESIS_TYPE,
                        location=f"thesis.types[{i}]",
                        detail=(
                            f"thesis type {thesis_type!r} does not resolve in the merged "
                            "thesis_types registry (bundled + overlay)"
                        ),
                    )
                )
        return findings

    def _check_external_sources(self, spec: AttackSpec) -> list[StaticSchemaFinding]:
        """Every external-data-source reference must resolve in the registry."""
        findings: list[StaticSchemaFinding] = []
        if spec.external_references is None:
            return findings
        for i, cve in enumerate(spec.external_references.cves):
            if cve.source_of_record is not None and (
                self._registries.external_source(cve.source_of_record) is None
            ):
                findings.append(
                    StaticSchemaFinding(
                        code=StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE,
                        location=f"external_references.cves[{i}].source_of_record",
                        detail=(
                            f"external data source {cve.source_of_record!r} does not resolve in "
                            "the external_data_sources registry"
                        ),
                    )
                )
        for i, adv in enumerate(spec.external_references.advisories):
            if self._registries.external_source(adv.source) is None:
                findings.append(
                    StaticSchemaFinding(
                        code=StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE,
                        location=f"external_references.advisories[{i}].source",
                        detail=(
                            f"external data source {adv.source!r} does not resolve in the "
                            "external_data_sources registry"
                        ),
                    )
                )
        return findings

    def _check_closed_catalog_membership(self, spec: AttackSpec) -> list[StaticSchemaFinding]:
        """Confirm each closed-enum value used also appears in its closed catalog.

        The enum already constrains the field at Pydantic-construction time; this
        check guards against *catalog/enum drift* (a value the enum admits but the
        bundled catalog omits) so the two stay in lockstep — a ``CATALOG_DRIFT``
        finding here means a bundled catalog is stale, which Layer 1 surfaces
        rather than silently tolerating.
        """
        findings: list[StaticSchemaFinding] = []
        severity_names = {e.name for e in self._get_severity_levels().entries}
        component_names = {e.name for e in self._get_detection_components().entries}
        format_names = {e.name for e in self._get_detection_formats().entries}
        mechanism_names = {e.name for e in self._get_provisioning_mechanisms().entries}

        if spec.chain is not None:
            for step in spec.chain.chain_steps:
                findings.extend(
                    self._check_mechanism(step.provisioning_mechanism, mechanism_names, step.id)
                )
                for d_i, detection in enumerate(step.detections):
                    loc = f"chain.chain_steps[{step.id}].detections[{d_i}]"
                    findings.extend(
                        self._check_detection(
                            detection.component,
                            detection.severity.value,
                            detection.formats,
                            component_names,
                            severity_names,
                            format_names,
                            loc,
                        )
                    )
        return findings

    def _check_mechanism(
        self,
        mechanism: ProvisioningMechanism,
        known: set[ProvisioningMechanism],
        step_id: str,
    ) -> list[StaticSchemaFinding]:
        if mechanism in known:
            return []
        return [
            StaticSchemaFinding(
                code=StaticSchemaCode.CATALOG_DRIFT,
                location=f"chain.chain_steps[{step_id}].provisioning_mechanism",
                detail=(
                    f"provisioning_mechanism {mechanism.value!r} is a valid enum member but is "
                    "absent from the bundled provisioning_mechanisms catalog (catalog drift)"
                ),
            )
        ]

    def _check_detection(
        self,
        component: DetectionComponent,
        severity: Severity,
        formats: Sequence[DetectionFormatEntry],
        known_components: set[DetectionComponent],
        known_severities: set[Severity],
        known_formats: set[DetectionFormat],
        location: str,
    ) -> list[StaticSchemaFinding]:
        findings: list[StaticSchemaFinding] = []
        if component not in known_components:
            findings.append(
                StaticSchemaFinding(
                    code=StaticSchemaCode.CATALOG_DRIFT,
                    location=f"{location}.component",
                    detail=(
                        f"detection component {component.value!r} is absent from the bundled "
                        "detection_components catalog (catalog drift)"
                    ),
                )
            )
        if severity not in known_severities:
            findings.append(
                StaticSchemaFinding(
                    code=StaticSchemaCode.CATALOG_DRIFT,
                    location=f"{location}.severity",
                    detail=(
                        f"severity {severity.value!r} is absent from the bundled severity_levels "
                        "catalog (catalog drift)"
                    ),
                )
            )
        for f_i, entry in enumerate(formats):
            if entry.format not in known_formats:
                findings.append(
                    StaticSchemaFinding(
                        code=StaticSchemaCode.CATALOG_DRIFT,
                        location=f"{location}.formats[{f_i}].format",
                        detail=(
                            f"detection format {entry.format.value!r} is absent from the bundled "
                            "detection_formats catalog (catalog drift)"
                        ),
                    )
                )
        return findings

    # --- lazy catalog loaders ---------------------------------------------

    def _get_severity_levels(self) -> SeverityLevelsCatalog:
        if self._severity_levels is None:
            from cyberlab_gen.registries.catalog_loader import load_severity_levels

            self._severity_levels = load_severity_levels()
        return self._severity_levels

    def _get_detection_components(self) -> DetectionComponentsCatalog:
        if self._detection_components is None:
            from cyberlab_gen.registries.catalog_loader import load_detection_components

            self._detection_components = load_detection_components()
        return self._detection_components

    def _get_detection_formats(self) -> DetectionFormatsCatalog:
        if self._detection_formats is None:
            from cyberlab_gen.registries.catalog_loader import load_detection_formats

            self._detection_formats = load_detection_formats()
        return self._detection_formats

    def _get_provisioning_mechanisms(self) -> ProvisioningMechanismsCatalog:
        if self._provisioning_mechanisms is None:
            from cyberlab_gen.registries.catalog_loader import load_provisioning_mechanisms

            self._provisioning_mechanisms = load_provisioning_mechanisms()
        return self._provisioning_mechanisms


__all__ = [
    "PendingProposals",
    "StaticSchemaCode",
    "StaticSchemaFinding",
    "StaticSchemaResult",
    "StaticSchemaValidator",
]
