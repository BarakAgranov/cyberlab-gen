"""Base-prompt-plus-overlay loader for agent prompts.

Per pipeline.md §3.5 ("Base prompt plus model-specific overlay"): each agent
ships a base prompt plus optional model-specific overlays. The provider resolves
a capability hint to a concrete model; the overlay keyed by that model is
appended to the base prompt. This keeps agent intent model-agnostic while
allowing targeted per-model tuning.

Tasks 3/5 store their prompts as files under each agent's directory using the
layout this loader expects:

    cyberlab_gen/agents/<agent>/prompt.md              # base prompt (required)
    cyberlab_gen/agents/<agent>/overlays/<model>.md    # optional overlay

The loader reads from the filesystem lazily (on each call). Prompts are small
text files; eager caching is a premature optimization (Task 2 decision
discretion: "whether prompts load eagerly or lazily" — chosen: lazily).
"""

from __future__ import annotations

from pathlib import Path

from cyberlab_gen.errors import ConfigError

#: Filename of an agent's base prompt within its directory.
BASE_PROMPT_FILENAME = "prompt.md"
#: Subdirectory holding model-specific overlay files.
OVERLAY_DIRNAME = "overlays"
#: Separator inserted between base prompt and overlay when both are present.
_OVERLAY_SEPARATOR = "\n\n---\n\n"


def _agents_root() -> Path:
    """Directory containing the agent packages (this module's directory)."""
    return Path(__file__).resolve().parent


def load_prompt(agent_dir: str, *, model: str | None = None) -> str:
    """Load an agent's base prompt, with the model-specific overlay appended.

    Args:
        agent_dir: The agent's directory name under ``cyberlab_gen/agents/``
            (e.g. ``"extractor"``).
        model: The concrete model identifier the provider resolved to. When
            given and an overlay file exists for that model, the overlay is
            appended to the base prompt. The model name is supplied by the
            framework after capability-hint resolution; agent code never
            hardcodes it.

    Returns:
        The base prompt, with the overlay appended (separated by a horizontal
        rule) when a matching overlay exists.

    Raises:
        ConfigError: if the base prompt file does not exist.
    """
    base_path = _agents_root() / agent_dir / BASE_PROMPT_FILENAME
    if not base_path.is_file():
        raise ConfigError(f"Base prompt not found for agent {agent_dir!r}: {base_path}")
    base = base_path.read_text(encoding="utf-8").strip()

    if model is None:
        return base

    overlay_path = _agents_root() / agent_dir / OVERLAY_DIRNAME / f"{model}.md"
    if not overlay_path.is_file():
        return base
    overlay = overlay_path.read_text(encoding="utf-8").strip()
    if not overlay:
        return base
    return f"{base}{_OVERLAY_SEPARATOR}{overlay}"
