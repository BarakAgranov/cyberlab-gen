"""Unit tests for ``Message._role_shape``.

Pins every invariant from ``provider-interface.md`` §4.1: role-specific
content requirements, ``tool_call_id`` placement, ``tool_calls``
placement, and the subtle valid case where ASSISTANT emits both text
content AND tool calls in a single turn.
"""

import pytest
from pydantic import ValidationError

from cyberlab_gen.providers.base import Message, MessageRole, ToolCall


def _tool_call() -> ToolCall:
    return ToolCall(call_id="call_1", tool_name="lookup", arguments={"q": "x"})


def test_system_message_requires_non_empty_content() -> None:
    with pytest.raises(ValidationError, match="non-empty content"):
        Message(role=MessageRole.SYSTEM, content="")


def test_user_message_requires_non_empty_content() -> None:
    with pytest.raises(ValidationError, match="non-empty content"):
        Message(role=MessageRole.USER, content="")


def test_tool_role_requires_tool_call_id() -> None:
    with pytest.raises(ValidationError, match="tool_call_id required"):
        Message(role=MessageRole.TOOL, content="result")


def test_tool_call_id_rejected_on_non_tool_roles() -> None:
    with pytest.raises(ValidationError, match="tool_call_id only valid"):
        Message(role=MessageRole.USER, content="hi", tool_call_id="call_1")


def test_tool_calls_rejected_on_non_assistant_roles() -> None:
    with pytest.raises(ValidationError, match="tool_calls only valid"):
        Message(role=MessageRole.USER, content="hi", tool_calls=[_tool_call()])


def test_assistant_can_have_empty_content_with_tool_calls() -> None:
    msg = Message(role=MessageRole.ASSISTANT, content="", tool_calls=[_tool_call()])
    assert msg.content == ""
    assert len(msg.tool_calls) == 1


def test_assistant_with_content_and_tool_calls_is_valid() -> None:
    """The model may emit text alongside tool calls in a single turn."""
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="Looking that up for you",
        tool_calls=[_tool_call()],
    )
    assert msg.content == "Looking that up for you"
    assert len(msg.tool_calls) == 1


def test_happy_path_each_role() -> None:
    system = Message(role=MessageRole.SYSTEM, content="be brief")
    user = Message(role=MessageRole.USER, content="hi")
    assistant = Message(role=MessageRole.ASSISTANT, content="hello")
    tool = Message(role=MessageRole.TOOL, content="result", tool_call_id="call_1")
    assert system.role is MessageRole.SYSTEM
    assert user.role is MessageRole.USER
    assert assistant.role is MessageRole.ASSISTANT
    assert tool.role is MessageRole.TOOL
    assert tool.tool_call_id == "call_1"


def test_message_is_frozen() -> None:
    msg = Message(role=MessageRole.USER, content="hi")
    with pytest.raises(ValidationError):
        msg.content = "mutated"  # pyright: ignore[reportAttributeAccessIssue]
