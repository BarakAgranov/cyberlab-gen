"""Unit tests for the provider error hierarchy.

Pins:

- All six provider error classes instantiate via the structured-context
  constructor (``message``, ``run_id``, ``cause`` kwargs).
- ``stage="provider"`` is auto-pinned by the ``ProviderError`` base.
- Each subtype is catchable as ``ProviderError`` AND ``CyberlabGenError``.
- ``UnmatchedMockCall`` is catchable as ``CyberlabGenError`` but NOT as
  ``ProviderError`` (it's a test-infra signal, not a real provider
  failure).
- ``raise X from Y`` populates ``__cause__``.
"""

import pytest

from cyberlab_gen.errors import (
    CapabilityUnreachable,
    CyberlabGenError,
    HardFailure,
    MalformedOutput,
    ProviderError,
    ToolLoopError,
    TransientFailure,
)
from cyberlab_gen.providers import UnmatchedMockCall

PROVIDER_ERROR_SUBTYPES: list[type[ProviderError]] = [
    TransientFailure,
    MalformedOutput,
    HardFailure,
    CapabilityUnreachable,
    ToolLoopError,
]


@pytest.mark.parametrize("cls", PROVIDER_ERROR_SUBTYPES)
def test_provider_error_subtype_pins_stage(cls: type[ProviderError]) -> None:
    err = cls("boom")
    assert err.stage == "provider"
    assert err.run_id is None
    assert err.cause is None


@pytest.mark.parametrize("cls", PROVIDER_ERROR_SUBTYPES)
def test_provider_error_subtype_is_catchable_as_base(cls: type[ProviderError]) -> None:
    with pytest.raises(ProviderError):
        raise cls("boom")
    with pytest.raises(CyberlabGenError):
        raise cls("boom")


def test_provider_error_carries_structured_context() -> None:
    underlying = RuntimeError("transport closed")
    err = TransientFailure("retries exhausted", run_id="run-123", cause=underlying)
    assert err.stage == "provider"
    assert err.run_id == "run-123"
    assert err.cause is underlying
    assert str(err) == "retries exhausted"


def test_raise_from_populates_dunder_cause() -> None:
    underlying = RuntimeError("transport closed")
    try:
        try:
            raise underlying
        except RuntimeError as exc:
            raise HardFailure("quota exceeded") from exc
    except HardFailure as raised:
        assert raised.__cause__ is underlying


def test_unmatched_mock_call_is_cyberlabgenerror_but_not_provider_error() -> None:
    err = UnmatchedMockCall("no registration")
    assert isinstance(err, CyberlabGenError)
    assert not isinstance(err, ProviderError)


def test_unmatched_mock_call_does_not_pin_provider_stage() -> None:
    err = UnmatchedMockCall("no registration")
    assert err.stage is None
