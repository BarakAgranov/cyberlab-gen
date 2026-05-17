"""Tests for ``ArtifactModel`` and ``InternalModel`` base classes.

Architectural source: ``schema-details.md`` §1.
"""

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import ArtifactModel, InternalModel


class _Artifact(ArtifactModel):
    name: str
    count: int


class _Internal(InternalModel):
    name: str


def test_artifact_model_rejects_unknown_field() -> None:
    """extra='forbid' must reject construction with unknown fields."""
    with pytest.raises(ValidationError) as exc:
        _Artifact.model_validate({"name": "ok", "count": 1, "bogus": "nope"})
    assert "bogus" in str(exc.value)


def test_internal_model_ignores_unknown_field() -> None:
    """extra='ignore' lets unknown fields pass without surfacing on the instance."""
    instance = _Internal.model_validate({"name": "ok", "unrecognized": 42})
    assert instance.name == "ok"
    assert not hasattr(instance, "unrecognized")


def test_artifact_model_validates_on_assignment() -> None:
    """validate_assignment=True must catch post-construction type violations."""
    instance = _Artifact(name="ok", count=1)
    with pytest.raises(ValidationError):
        instance.count = "not an int"  # type: ignore[assignment]


def test_artifact_model_strips_string_whitespace() -> None:
    """str_strip_whitespace=True must normalize string fields at validation."""
    instance = _Artifact(name="  ok  ", count=1)
    assert instance.name == "ok"


def test_internal_model_does_not_validate_on_assignment() -> None:
    """InternalModel's relaxed config (validate_assignment=False) allows in-place mutation
    without re-running validators - the baseline for internal scratch types.
    """
    instance = _Internal(name="ok")
    instance.name = 123  # type: ignore[assignment]
    assert instance.name == 123  # type: ignore[comparison-overlap]


def test_artifact_model_yaml_round_trip() -> None:
    """to_yaml() and from_yaml() must round-trip an artifact losslessly."""
    original = _Artifact(name="hello", count=3)
    serialized = original.to_yaml()
    assert "name: hello" in serialized
    assert "count: 3" in serialized
    restored = _Artifact.from_yaml(serialized)
    assert restored == original


def test_artifact_model_from_yaml_rejects_unknown_field() -> None:
    """from_yaml() must inherit extra='forbid' rejection from validation."""
    raw_yaml = "name: ok\ncount: 1\nbogus: nope\n"
    with pytest.raises(ValidationError) as exc:
        _Artifact.from_yaml(raw_yaml)
    assert "bogus" in str(exc.value)
