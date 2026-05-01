# tests/test_core/test_orchestrator.py
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator, Settings, Router, GatewayClient, IntentRerankerService
from core.events import TextDeltaEvent, ToolCallStartEvent, ToolCallDeltaEvent, SystemToolCallEvent, WorkflowFinishEvent, ErrorEvent
from core.schema import InternalAgentRequest, InternalMessage, InternalTool
from core.llm_client import StandardLLMChunk, ToolCallChunk
import mcp.types as types

def test_intent_reranker_service():
    with patch("core.orchestrator.CrossEncoder") as mock_cross_encoder:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.95, 0.12]
        mock_cross_encoder.return_value = mock_model
        
        IntentRerankerService._instance = None
        
        reranker = IntentRerankerService()
        result = reranker.rerank("test query", ["doc 1", "doc 2"])
        
        mock_cross_encoder.assert_called_once_with("BAAI/bge-reranker-v2-m3")
        assert result[0]["document"] == "doc 1"
        assert result[0]["score"] == 0.95
        assert result[1]["document"] == "doc 2"
        assert result[1]["score"] == 0.12
        
        reranker2 = IntentRerankerService()
        assert reranker is reranker2
        assert mock_cross_encoder.call_count == 1

@pytest.mark.asyncio
async def test_router_route():
    settings = Settings()
    
    with patch("pathlib.Path.glob") as mock_glob, \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load") as mock_yaml_load, \
         patch("core.orchestrator.IntentRerankerService") as mock_reranker_cls:
         
        mock_path1 = MagicMock()
        mock_path1.stem = "coder"
        mock_path2 = MagicMock()
        mock_path2.stem = "interlocutor"
        mock_glob.return_value = [mock_path1, mock_path2]
        
        mock_yaml_load.side_effect = [
            {"name": "coder", "description": "Write code"},
            {"name": "interlocutor", "description": "Chat with user"}
        ]
        
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [
            {"document": "Chat with user", "score": 0.9},
            {"document": "Write code", "score": 0.1}
        ]
        mock_reranker_cls.return_value = mock_reranker
        
        mock_orch = MagicMock()
        mock_orch._extract_intent = AsyncMock(return_value="Chat with user")
        
        router = Router(settings, Path("dummy"), orchestrator=mock_orch)
        
        messages = [InternalMessage(role="user", content="Hello")]
        selected = await router.route(messages)
        
        assert selected == "interlocutor"
        mock_orch._extract_intent.assert_called_once()
        mock_reranker.rerank.assert_called_once()
        args, _ = mock_reranker.rerank.call_args
        assert args[0] == "Chat with user"

@pytest.fixture
def mock_gateway():
    with patch("core.orchestrator.GatewayClient") as mock:
        inst = mock.return_value
        inst.start = AsyncMock()
        inst.stop = AsyncMock()
        inst.fetch_tools = AsyncMock(return_value=[{"name": "test_tool", "description": "desc", "inputSchema": {}}])
        inst.call_tool = AsyncMock(return_value="Success")
        yield inst

@pytest.fixture
def orchestrator(mock_gateway):
    with patch("pathlib.Path.exists", return_value=False):
        o = Orchestrator("dummy.yaml")
        o.router.route = AsyncMock(return_value="interlocutor")
        return o

@pytest.mark.asyncio
async def test_start_shutdown(orchestrator, mock_gateway):
    await orchestrator.start()
    mock_gateway.start.assert_called_once()
    await orchestrator.shutdown()
    mock_gateway.stop.assert_called_once()

@pytest.mark.asyncio
async def test_extract_intent(orchestrator):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Asking a question"}}]
    }
    
    orchestrator.http_client.post = AsyncMock(return_value=mock_resp)
    
    intent = await orchestrator._extract_intent("こんにちは、これは何ですか？")
    assert intent == "Asking a question"
    orchestrator.http_client.post.assert_called_once()

@pytest.mark.asyncio
async def test_extract_intent_error(orchestrator):
    orchestrator.http_client.post = AsyncMock(side_effect=Exception("Network Error"))
    
    intent = await orchestrator._extract_intent("こんにちは")
    assert intent == "Unknown intent"

@pytest.mark.asyncio
async def test_run_workflow_interlocutor(orchestrator):
    # httpxのモックではなく、LLMClientProtocolのモックを使用する
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(content="Hello")
        yield StandardLLMChunk(finish_reason="stop")
        
    orchestrator.llm_client.stream_chat = mock_stream_chat

    yaml_data = {"name": "interlocutor", "steps": [{"type": "llm_chat", "model_key": "interlocutor"}]}
    
    with patch("builtins.open", MagicMock()), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("yaml.safe_load", return_value=yaml_data):
        
        req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Hi")])
        events = [e async for e in orchestrator.process_workflow(req)]
        
        assert len(events) == 2
        assert isinstance(events[0], TextDeltaEvent)
        assert events[0].content == "Hello"
        assert isinstance(events[1], WorkflowFinishEvent)
        assert events[1].finish_reason == "stop"

@pytest.mark.asyncio
async def test_run_workflow_agent_task(orchestrator, mock_gateway):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it", "model_key": "coder"}]}

    with patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=yaml_data), \
         patch("core.orchestrator.OpenAIServerModel"), \
         patch("core.orchestrator.ToolCallingAgent") as mock_agent:
        
        mock_agent.return_value.run.return_value = "Task Finished"
        
        req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Fix bug")])
        events = [e async for e in orchestrator.process_workflow(req)]
        
        assert len(events) == 3
        assert isinstance(events[0], TextDeltaEvent) and "[Step 1 Start]" in events[0].content
        assert isinstance(events[1], TextDeltaEvent) and "Task Finished" in events[1].content
        assert isinstance(events[2], WorkflowFinishEvent)

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it"}]}

    with patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=yaml_data):
        
        req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Fix bug")])
        events = [e async for e in orchestrator.process_workflow(req)]
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_workflow_file_not_found(orchestrator):
    with patch("pathlib.Path.exists", return_value=False):
        req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Hi")])
        events = [e async for e in orchestrator.process_workflow(req)]
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_call_llm_stream_reflection_dynamic(orchestrator):
    """ツール呼び出しがなく、テキストのみが返された場合のリフレクション(Reranker)のテスト"""
    
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(content="Hello")
        yield StandardLLMChunk(content=" World")
        yield StandardLLMChunk(finish_reason="stop")
        
    orchestrator.llm_client.stream_chat = mock_stream_chat

    with patch("core.orchestrator.IntentRerankerService") as mock_reranker_cls:
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [
            {"document": "Concludes the interaction", "score": 0.99}
        ]
        mock_reranker_cls.return_value = mock_reranker
        
        orchestrator._extract_intent = AsyncMock(return_value="Conclude interaction")

        req = InternalAgentRequest(
            messages=[InternalMessage(role="user", content="Hi")],
            tools=[InternalTool(
                type="function",
                function={
                    "name": "custom_finish_tool",
                    "description": "Concludes the interaction",
                    "parameters": {
                        "properties": {"summary": {"type": "string"}, "is_done": {"type": "boolean"}},
                        "required": ["summary", "is_done"]
                    }
                }
            )]
        )

        events = [e async for e in orchestrator._call_llm("interlocutor", "http://dummy", req)]
        
        assert len(events) == 4
        assert isinstance(events[0], TextDeltaEvent) and events[0].content == "Hello"
        assert isinstance(events[1], TextDeltaEvent) and events[1].content == " World"
        # リフレクションによってツール呼び出しが自動生成される
        assert isinstance(events[2], SystemToolCallEvent) and events[2].tool_name == "custom_finish_tool"
        
        assert events[2].arguments.get("summary") == "Hello World"
        assert events[2].arguments.get("is_done") is False
        assert isinstance(events[3], WorkflowFinishEvent) and events[3].finish_reason == "tool_calls"
        
        args_call, _ = mock_reranker.rerank.call_args
        assert args_call[0] == "Conclude interaction"

@pytest.mark.asyncio
async def test_call_llm_stream_fallback_rewrite(orchestrator):
    """利用不可能なツール名が返ってきた場合に、強制書き換え(ハルシネーション対策)が発動するかのテスト"""
    
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(
            tool_calls=[ToolCallChunk(index=0, id="call_1", name="hallucinated_tool", arguments='{"text": "Sure!"}')],
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