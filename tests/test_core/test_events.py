# tests/test_core/test_events.py
import pytest
from core.events import (
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)

def test_text_delta_event():
    event = TextDeltaEvent(content="Hello")
    assert event.content == "Hello"

def test_tool_call_start_event():
    event = ToolCallStartEvent(index=0, id="call_123", tool_name="my_tool")
    assert event.index == 0
    assert event.id == "call_123"
    assert event.tool_name == "my_tool"

def test_tool_call_delta_event():
    event = ToolCallDeltaEvent(index=0, arguments='{"key": "value"}')
    assert event.index == 0
    assert event.arguments == '{"key": "value"}'

def test_system_tool_call_event():
    event = SystemToolCallEvent(index=1, id="sys_1", tool_name="sys_tool", arguments={"key": "val"})
    assert event.tool_name == "sys_tool"
    assert event.arguments == {"key": "val"}

def test_workflow_finish_event_default():
    event = WorkflowFinishEvent()
    assert event.finish_reason == "stop"

def test_workflow_finish_event_custom():
    event = WorkflowFinishEvent(finish_reason="tool_calls")
    assert event.finish_reason == "tool_calls"

def test_error_event():
    event = ErrorEvent(message="Something went wrong")
    assert event.message == "Something went wrong"