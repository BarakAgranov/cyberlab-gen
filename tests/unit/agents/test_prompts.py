"""Tests for the base-prompt-plus-overlay loader (pipeline.md §3.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.agents import prompts
from cyberlab_gen.agents.prompts import load_prompt
from cyberlab_gen.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


def test_loads_base_prompt_for_seeded_agent() -> None:
    text = load_prompt("extractor")
    assert "Extractor" in text
    assert text == text.strip()  # leading/trailing whitespace stripped


def test_missing_base_prompt_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="Base prompt not found"):
        load_prompt("no_such_agent_dir")


def test_model_with_no_overlay_returns_base_unchanged() -> None:
    base = load_prompt("extractor")
    with_model = load_prompt("extractor", model="some-unrelated-model")
    assert with_model == base


def test_overlay_is_appended_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Build a throwaway agent tree so the test never depends on a real overlay
    # file shipping in the package.
    agent_dir = tmp_path / "fake_agent"
    (agent_dir / prompts.OVERLAY_DIRNAME).mkdir(parents=True)
    (agent_dir / prompts.BASE_PROMPT_FILENAME).write_text("BASE TEXT", encoding="utf-8")
    (agent_dir / prompts.OVERLAY_DIRNAME / "some-model.md").write_text(
        "OVERLAY TEXT", encoding="utf-8"
    )
    monkeypatch.setattr(prompts, "_agents_root", lambda: tmp_path)

    base_only = load_prompt("fake_agent")
    assert base_only == "BASE TEXT"

    combined = load_prompt("fake_agent", model="some-model")
    assert combined.startswith("BASE TEXT")
    assert combined.endswith("OVERLAY TEXT")
    assert combined != "BASE TEXT"  # overlay actually changed the result


def test_empty_overlay_returns_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent_dir = tmp_path / "fake_agent"
    (agent_dir / prompts.OVERLAY_DIRNAME).mkdir(parents=True)
    (agent_dir / prompts.BASE_PROMPT_FILENAME).write_text("BASE", encoding="utf-8")
    (agent_dir / prompts.OVERLAY_DIRNAME / "m.md").write_text("   \n  ", encoding="utf-8")
    monkeypatch.setattr(prompts, "_agents_root", lambda: tmp_path)

    assert load_prompt("fake_agent", model="m") == "BASE"
