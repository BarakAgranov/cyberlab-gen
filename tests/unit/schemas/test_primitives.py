"""Tests for the constrained-string primitives in ``schemas/primitives.py``.

Architectural source: ``schema-details.md`` §2.1. Each primitive's regex is a
documented contract; these tests pin one accepting and one rejecting case so
a future edit to the pattern can't drift silently.
"""

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import (
    ArtifactModel,
    FacetName,
    KebabId,
    NonEmptyString,
    SemVer,
    Sha256Hex,
    SnakeName,
    TradecraftName,
)


class _KebabHolder(ArtifactModel):
    v: KebabId


class _SnakeHolder(ArtifactModel):
    v: SnakeName


class _FacetHolder(ArtifactModel):
    v: FacetName


class _TradecraftHolder(ArtifactModel):
    v: TradecraftName


class _NonEmptyHolder(ArtifactModel):
    v: NonEmptyString


class _SemVerHolder(ArtifactModel):
    v: SemVer


class _Sha256Holder(ArtifactModel):
    v: Sha256Hex


def test_kebab_id_accepts_kebab_case() -> None:
    assert _KebabHolder(v="aws-credentials-v2").v == "aws-credentials-v2"


def test_kebab_id_rejects_underscore() -> None:
    with pytest.raises(ValidationError):
        _KebabHolder(v="aws_credentials")


def test_snake_name_accepts_snake_case() -> None:
    assert _SnakeHolder(v="aws_credentials").v == "aws_credentials"


def test_snake_name_rejects_kebab() -> None:
    with pytest.raises(ValidationError):
        _SnakeHolder(v="aws-credentials")


def test_snake_name_rejects_leading_digit() -> None:
    with pytest.raises(ValidationError):
        _SnakeHolder(v="1foo")


def test_facet_name_accepts_target_prefix() -> None:
    assert _FacetHolder(v="target:aws").v == "target:aws"


def test_facet_name_accepts_runtime_prefix() -> None:
    assert _FacetHolder(v="runtime:kubernetes").v == "runtime:kubernetes"


def test_facet_name_accepts_lab_class_signal_prefix() -> None:
    assert _FacetHolder(v="lab_class_signal:multi_phase").v == "lab_class_signal:multi_phase"


def test_facet_name_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        _FacetHolder(v="cloud:aws")


def test_tradecraft_name_accepts_mitre_style() -> None:
    assert _TradecraftHolder(v="mitre:t1078").v == "mitre:t1078"


def test_tradecraft_name_rejects_missing_prefix() -> None:
    with pytest.raises(ValidationError):
        _TradecraftHolder(v="t1078")


def test_non_empty_string_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        _NonEmptyHolder(v="")


def test_non_empty_string_accepts_one_char() -> None:
    assert _NonEmptyHolder(v="x").v == "x"


def test_semver_accepts_basic() -> None:
    assert _SemVerHolder(v="1.2.3").v == "1.2.3"


def test_semver_accepts_prerelease() -> None:
    assert _SemVerHolder(v="1.2.3-rc1").v == "1.2.3-rc1"


def test_semver_rejects_two_segments() -> None:
    with pytest.raises(ValidationError):
        _SemVerHolder(v="1.2")


def test_sha256_accepts_lowercase_hex() -> None:
    digest = "a" * 64
    assert _Sha256Holder(v=digest).v == digest


def test_sha256_rejects_uppercase_hex() -> None:
    with pytest.raises(ValidationError):
        _Sha256Holder(v="A" * 64)


def test_sha256_rejects_wrong_length() -> None:
    with pytest.raises(ValidationError):
        _Sha256Holder(v="a" * 63)
