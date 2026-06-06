"""Tests for local Phoenix tracing setup (ADR 0041).

The one guarantee that must hold without a Phoenix (or the observability extra)
present: ``setup_tracing`` never raises and no-ops cleanly when tracing is off or
Phoenix is unreachable, and ``stage_span`` is a harmless no-op in that state — so a
normal run is completely unaffected. These paths are exercised offline (no Phoenix,
no OTel imports needed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.tracing_setup import (
    reset_tracing_for_tests,
    setup_tracing,
    stage_span,
)

if TYPE_CHECKING:
    import pytest


def test_tracing_off_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERLAB_GEN_TRACING", "off")
    reset_tracing_for_tests()
    assert setup_tracing() is False


def test_auto_no_phoenix_is_disabled_and_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # auto mode + an endpoint with nothing listening → disabled, no exception, no
    # OTel import cost (the probe fails before any heavy import).
    monkeypatch.setenv("CYBERLAB_GEN_TRACING", "auto")
    reset_tracing_for_tests()
    assert setup_tracing(endpoint="http://127.0.0.1:65535") is False


def test_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERLAB_GEN_TRACING", "off")
    reset_tracing_for_tests()
    assert setup_tracing() is False
    assert setup_tracing() is False  # second call returns the same verdict, no stacking


def test_stage_span_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # With tracing disabled, the orchestrator's per-node stage_span must be a clean
    # no-op so a run completes normally with no Phoenix.
    monkeypatch.setenv("CYBERLAB_GEN_TRACING", "off")
    reset_tracing_for_tests()
    setup_tracing()
    ran = False
    with stage_span("extract"):
        ran = True
    assert ran
