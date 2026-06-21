"""Tests for the closed bundled-only catalog meta-schemas.

Architectural source: ``registry-details.md`` §7; cross-checked against the
``StrEnum`` membership in ``cyberlab_gen.schemas.enums``. Fixtures mirror the
canonical §7 seeds.
"""

import pytest
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from cyberlab_gen.registries.loader import bundled_registry_dir
from cyberlab_gen.schemas.catalogs import (
    DetectionComponentEntry,
    DetectionComponentsCatalog,
    DetectionFormatEntry,
    DetectionFormatsCatalog,
    ProvisioningMechanismEntry,
    ProvisioningMechanismsCatalog,
    SeverityLevelEntry,
    SeverityLevelsCatalog,
)
from cyberlab_gen.schemas.enums import (
    DetectionComponent,
    DetectionFormat,
    ProvisioningMechanism,
    Severity,
)

# --- Per-entry happy paths -------------------------------------------------


def test_detection_component_entry_constructs() -> None:
    entry = DetectionComponentEntry(
        name=DetectionComponent.CSPM,
        display_name="Cloud Security Posture Management",
        description="Misconfiguration and posture detection across cloud accounts.",
    )
    assert entry.name is DetectionComponent.CSPM


def test_severity_level_entry_constructs_with_ordinal() -> None:
    entry = SeverityLevelEntry(name=Severity.CRITICAL, ordinal=4)
    assert entry.name is Severity.CRITICAL
    assert entry.ordinal == 4


def test_detection_format_entry_constructs() -> None:
    entry = DetectionFormatEntry(
        name=DetectionFormat.SIGMA,
        display_name="Sigma",
        file_extension=".yml",
        description="Portable, SIEM-agnostic detection format.",
    )
    assert entry.name is DetectionFormat.SIGMA
    assert entry.file_extension == ".yml"


def test_provisioning_mechanism_entry_constructs() -> None:
    entry = ProvisioningMechanismEntry(
        name=ProvisioningMechanism.TERRAFORM,
        display_name="Terraform",
        description="HashiCorp Terraform.",
        validator_support="full",
    )
    assert entry.name is ProvisioningMechanism.TERRAFORM
    assert entry.validator_support == "full"


# --- Enum membership is owned by the enum, not the catalog -----------------


def test_detection_component_rejects_value_not_in_enum() -> None:
    """A name the ``DetectionComponent`` enum doesn't know fails validation.

    Pins that catalog membership is owned by the enum (no duplicate source of
    truth): the YAML cannot introduce a component the enum hasn't declared.
    """
    with pytest.raises(ValidationError):
        DetectionComponentEntry.model_validate(
            {"name": "NOT_A_REAL_COMPONENT", "display_name": "x", "description": "y"}
        )


def test_detection_format_rejects_value_not_in_enum() -> None:
    with pytest.raises(ValidationError):
        DetectionFormatEntry.model_validate(
            {
                "name": "splunk_spl",
                "display_name": "x",
                "file_extension": ".x",
                "description": "y",
            }
        )


def test_provisioning_mechanism_rejects_value_not_in_enum() -> None:
    with pytest.raises(ValidationError):
        ProvisioningMechanismEntry.model_validate(
            {
                "name": "pulumi",
                "display_name": "x",
                "description": "y",
                "validator_support": "full",
            }
        )


# --- Field-level constraints -----------------------------------------------


@pytest.mark.parametrize("bad_ordinal", [0, 5, -1, 99])
def test_severity_level_rejects_ordinal_out_of_range(bad_ordinal: int) -> None:
    with pytest.raises(ValidationError):
        SeverityLevelEntry(name=Severity.LOW, ordinal=bad_ordinal)


def test_provisioning_mechanism_rejects_unknown_validator_support() -> None:
    with pytest.raises(ValidationError):
        ProvisioningMechanismEntry.model_validate(
            {
                "name": "terraform",
                "display_name": "x",
                "description": "y",
                "validator_support": "comprehensive",  # not in the Literal
            }
        )


# --- extra='forbid' inherited from ArtifactModel ---------------------------


def test_detection_component_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        DetectionComponentEntry.model_validate(
            {"name": "CSPM", "display_name": "x", "description": "y", "bogus": 1}
        )
    assert "bogus" in str(exc.value)


# --- Per-catalog containers ------------------------------------------------


@pytest.mark.parametrize(
    "catalog_cls",
    [
        DetectionComponentsCatalog,
        SeverityLevelsCatalog,
        DetectionFormatsCatalog,
        ProvisioningMechanismsCatalog,
    ],
)
def test_catalog_accepts_empty_entries(catalog_cls: type[BaseModel]) -> None:
    instance = catalog_cls.model_validate({"entries": []})
    assert getattr(instance, "entries") == []  # noqa: B009


def test_catalog_rejects_proposals_block() -> None:
    """Closed catalogs are not proposal-flow registries: no ``proposals:`` key.

    The inherited ``extra='forbid'`` rejects a stray overlay-style block.
    """
    with pytest.raises(ValidationError) as exc:
        DetectionComponentsCatalog.model_validate({"entries": [], "proposals": {}})
    assert "proposals" in str(exc.value)


# --- Bundled-seed smoke check (the un-deferred Phase-0 check 4) -------------
#
# implementation-plan.md §3.4 check 4 deferred these five closed catalogs
# "once those get Pydantic models." ADR 0016 added the models and seeds; this
# is the un-deferral: each `registry/<name>.yaml` seed loads and validates
# against its `catalogs.py` model, mirroring the
# `tests/integration/test_registry_load.py` pattern for the six runtime
# registries. A drift between an enum, a model, and its YAML seed fails here.

_yaml = YAML(typ="safe")

# (yaml file stem, catalog container model, expected entry count per ADR 0016)
_CLOSED_CATALOGS: list[tuple[str, type[BaseModel], int]] = [
    ("detection_components", DetectionComponentsCatalog, 10),
    ("severity_levels", SeverityLevelsCatalog, 4),
    ("detection_formats", DetectionFormatsCatalog, 4),
    ("provisioning_mechanisms", ProvisioningMechanismsCatalog, 7),
]


@pytest.mark.parametrize(
    ("stem", "catalog_cls", "expected_count"),
    _CLOSED_CATALOGS,
    ids=[stem for stem, _, _ in _CLOSED_CATALOGS],
)
def test_closed_catalog_yaml_loads_through_model(
    stem: str, catalog_cls: type[BaseModel], expected_count: int
) -> None:
    """The bundled ``registry/<stem>.yaml`` seed validates against its model.

    Loads the seed and validates it through the catalog container model
    (which validates every entry, incl. enum membership for the four
    enum-keyed catalogs). The count assertion pins the ADR 0016 seed sizes so
    an accidental add/drop of a seed entry is caught.
    """
    path = bundled_registry_dir() / f"{stem}.yaml"
    with path.open("r", encoding="utf-8") as handle:
        mapping = _yaml.load(handle)
    catalog = catalog_cls.model_validate(mapping)
    entries = getattr(catalog, "entries")  # noqa: B009
    assert len(entries) == expected_count


def test_severity_levels_seed_has_all_four_ordinals() -> None:
    """The severity seed maps each ``Severity`` member to a distinct ordinal.

    The containerized dry-run's severity-floor rules read ``ordinal``; a missing or duplicated
    ordinal would break cross-severity comparison.
    """
    path = bundled_registry_dir() / "severity_levels.yaml"
    with path.open("r", encoding="utf-8") as handle:
        mapping = _yaml.load(handle)
    catalog = SeverityLevelsCatalog.model_validate(mapping)
    by_name = {entry.name: entry.ordinal for entry in catalog.entries}
    assert set(by_name) == set(Severity)
    assert sorted(by_name.values()) == [1, 2, 3, 4]
