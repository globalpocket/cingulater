# tests/test_core/test_orchestrator.py
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator, Settings
from core.events import TextDeltaEvent, ToolCallStartEvent, ToolCallDeltaEvent, SystemToolCallEvent, WorkflowFinishEvent, ErrorEvent
from core.schema import InternalAgentRequest, InternalMessage, InternalTool
from core.llm_client import StandardLLMChunk, ToolCallChunk
import mcp.types as types

@pytest.fixture
def orchestrator():
    with patch("pathlib.Path.exists", return_value=False):
        o = Orchestrator("dummy.yaml")
        return o

@pytest.mark.asyncio
async def test_start_shutdown(orchestrator):
    await orchestrator.start()
    await orchestrator.shutdown()

@pytest.mark.asyncio
async def test_run_workflow_interlocutor(orchestrator):
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(content="Hello")
        yield StandardLLMChunk(finish_reason="stop")
        
    orchestrator.llm_client.stream_chat = mock_stream_chat

    req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Hi")])
    events = [e async for e in orchestrator.process_workflow(req)]
    
    assert len(events) == 2
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].content == "Hello"
    assert isinstance(events[1], WorkflowFinishEvent)
    assert events[1].finish_reason == "stop"

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    # _raw_run_workflowを直接呼び出してエラーケースをテストする
    req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Fix bug")])
    events = [e async for e in orchestrator._raw_run_workflow("interlocutor", req, workflow_steps=[{"type": "llm_chat"}])]
    
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_call_llm_stream_fallback_rewrite(orchestrator):
    """利用不可能なツール名が返ってきた場合に、強制書き換え(ハルシネーション対策)が発動するかのテスト"""
    
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(
            tool_calls=[ToolCallChunk(index=0, id="call_1", name="hallucinated_tool")]
        )
        yield StandardLLMChunk(
            tool_calls=[ToolCallChunk(index=0, arguments='{"text": "Sure!"}')],
            finish_reason="tool_calls"
        )
        
    orchestrator.llm_client.stream_chat = mock_stream_chat
    
    req = InternalAgentRequest(
        messages=[InternalMessage(role="user", content="Hi")],
        tools=[InternalTool(
            type="function",
            function={
                "name": "valid_client_tool",
                "parameters": {"properties": {"msg": {"type": "string"}}, "required": ["msg"]}
            }
        )]
    )

    events = [e async for e in orchestrator._call_llm("interlocutor", "http://dummy", req)]
    
    assert len(events) == 2
    assert isinstance(events[0], SystemToolCallEvent) and events[0].tool_name == "valid_client_tool"
    assert events[0].arguments.get("msg") == "Sure!"
    assert isinstance(events[1], WorkflowFinishEvent)