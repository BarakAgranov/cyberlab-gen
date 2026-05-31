"""Agent layer public surface.

Tasks 3/5 import the call surface and prompt loader from here, never from a
submodule directly. See pipeline.md §3.5 and dev/decisions/0017.
"""

from __future__ import annotations

from cyberlab_gen.agents.call_surface import (
    DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    AgentRunner,
)
from cyberlab_gen.agents.prompts import (
    BASE_PROMPT_FILENAME,
    OVERLAY_DIRNAME,
    load_prompt,
)

__all__ = [
    "BASE_PROMPT_FILENAME",
    "DEFAULT_STRUCTURAL_RETRY_ATTEMPTS",
    "OVERLAY_DIRNAME",
    "AgentRunner",
    "load_prompt",
]
