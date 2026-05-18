"""Integration tests for the Phase-0 ``AnthropicProvider`` scaffold.

Pins:

- Module imports cleanly (proves the ``anthropic`` SDK resolves).
- ``name`` returns ``"anthropic"``.
- Both call methods raise ``NotImplementedError("Phase 1")``.
"""

import asyncio

import pytest
from pydantic import BaseModel

from cyberlab_gen.providers import (
    AgentLabel,
    AnthropicProvider,
    CapabilityHint,
    Message,
    MessageRole,
)


class _Probe(BaseModel):
    field: str = "value"


def _executor() -> object:
    # ToolExecutor protocol; the scaffold never reaches the call.
    class _NeverCalled:
        async def execute(self, call: object) -> object:
            raise AssertionError("scaffold should not invoke the executor")

    return _NeverCalled()


def test_anthropic_provider_name() -> None:
    assert AnthropicProvider().name == "anthropic"


def test_complete_raises_phase_1_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match=r"^Phase 1$"):
        asyncio.run(
            AnthropicProvider().complete(
                [Message(role=MessageRole.USER, content="hi")],
                output_schema=_Probe,
                capability=CapabilityHint.HIGH_QUALITY_REASONING,
                agent_label=AgentLabel.EXTRACTOR,
            )
        )


def test_complete_with_tools_raises_phase_1_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match=r"^Phase 1$"):
        asyncio.run(
            AnthropicProvider().complete_with_tools(
                [Message(role=MessageRole.USER, content="hi")],
                output_schema=_Probe,
                capability=CapabilityHint.HIGH_QUALITY_REASONING,
                tools=[],
                tool_executor=_executor(),  # type: ignore[arg-type]
                agent_label=AgentLabel.EXTRACTOR,
                max_iterations=3,
            )
        )
