# tests/test_core/test_orchestrator.py
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator, Settings, Router, GatewayClient, IntentClassifierService
from core.events import TextDeltaEvent, ToolCallStartEvent, ToolCallDeltaEvent, SystemToolCallEvent, WorkflowFinishEvent, ErrorEvent
import mcp.types as types

def test_intent_classifier_service():
    with patch("core.orchestrator.pipeline") as mock_pipeline:
        mock_classifier = MagicMock()
        mock_classifier.return_value = {"labels": ["doc 1", "doc 2"], "scores": [0.95, 0.12]}
        mock_pipeline.return_value = mock_classifier
        
        IntentClassifierService._instance = None
        
        classifier = IntentClassifierService()
        result = classifier.classify("test query", ["doc 1", "doc 2"])
        
        mock_pipeline.assert_called_once_with("zero-shot-classification", model="facebook/bart-large-mnli")
        assert result["scores"] == [0.95, 0.12]
        assert result["labels"] == ["doc 1", "doc 2"]
        
        classifier2 = IntentClassifierService()
        assert classifier is classifier2
        assert mock_pipeline.call_count == 1

@pytest.mark.asyncio
async def test_router_route():
    settings = Settings()
    
    with patch("pathlib.Path.glob") as mock_glob, \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load") as mock_yaml_load, \
         patch("core.orchestrator.IntentClassifierService") as mock_classifier_cls:
         
        mock_path1 = MagicMock()
        mock_path1.stem = "coder"
        mock_path2 = MagicMock()
        mock_path2.stem = "interlocutor"
        mock_glob.return_value = [mock_path1, mock_path2]
        
        mock_yaml_load.side_effect = [
            {"name": "coder", "description": "Write code"},
            {"name": "interlocutor", "description": "Chat with user"}
        ]
        
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = {
            "labels": ["Expert: interlocutor\nDescription: Chat with user", "Expert: coder\nDescription: Write code"],
            "scores": [0.9, 0.1]
        }
        mock_classifier_cls.return_value = mock_classifier
        
        mock_orch = MagicMock()
        mock_orch._extract_intent = AsyncMock(return_value="Chat with user")
        
        router = Router(settings, Path("dummy"), orchestrator=mock_orch)
        selected = await router.route([{"role": "user", "content": "Hello"}])
        
        assert selected == "interlocutor"
        mock_orch._extract_intent.assert_called_once()
        mock_classifier.classify.assert_called_once()
        args, _ = mock_classifier.classify.call_args
        assert "User Intent: Chat with user" in args[0]

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
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        'data: {"choices": [{"delta": {"role": "assistant", "content": "Hello"}, "finish_reason": null}]}\n\n',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n',
        'data: [DONE]\n\n'
    ]
    async def aiter_lines():
        for line in stream_data: yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    yaml_data = {"name": "interlocutor", "steps": [{"type": "llm_chat", "model_key": "interlocutor"}]}
    
    with patch("builtins.open", MagicMock()):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
                    events = [e async for e in orchestrator.process_workflow({"messages": [{"role": "user", "content": "Hi"}]})]
                    assert len(events) == 2
                    assert isinstance(events[0], TextDeltaEvent)
                    assert events[0].content == "Hello"
                    assert isinstance(events[1], WorkflowFinishEvent)
                    assert events[1].finish_reason == "stop"

@pytest.mark.asyncio
async def test_run_workflow_agent_task(orchestrator, mock_gateway):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it", "model_key": "coder"}]}

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("core.orchestrator.OpenAIServerModel"):
                    with patch("core.orchestrator.ToolCallingAgent") as mock_agent:
                        mock_agent.return_value.run.return_value = "Task Finished"
                        events = [e async for e in orchestrator.process_workflow({"messages": [{"role": "user", "content": "Fix bug"}]})]
                        
                        assert len(events) == 3
                        assert isinstance(events[0], TextDeltaEvent) and "[Step 1 Start]" in events[0].content
                        assert isinstance(events[1], TextDeltaEvent) and "Task Finished" in events[1].content
                        assert isinstance(events[2], WorkflowFinishEvent)

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it"}]}

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                events = [e async for e in orchestrator.process_workflow({"messages": [{"role": "user", "content": "Fix bug"}]})]
                assert len(events) == 1
                assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_workflow_file_not_found(orchestrator):
    with patch("pathlib.Path.exists", return_value=False):
        events = [e async for e in orchestrator.process_workflow({"messages": [{"role": "user", "content": "Hi"}]})]
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_call_llm_stream_reflection_dynamic(orchestrator):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        'data: {"choices": [{"delta": {"role": "assistant", "content": "Hello"}}]}\n\n',
        'data: {"choices": [{"delta": {"content": " World"}}]}\n\n',
        'data: [DONE]\n\n'
    ]
    async def aiter_lines():
        for line in stream_data: yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx), \
         patch("core.orchestrator.IntentClassifierService") as mock_classifier_cls:
         
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = {
            "labels": ["Tool: custom_finish_tool\nDescription: Concludes the interaction"],
            "scores": [0.99]
        }
        mock_classifier_cls.return_value = mock_classifier
        
        # モックを追加して、ネットワーク呼び出しをスキップさせる
        orchestrator._extract_intent = AsyncMock(return_value="Conclude interaction")

        events = [e async for e in orchestrator._call_llm("interlocutor", "http://dummy", {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{
                "type": "function", 
                "function": {
                    "name": "custom_finish_tool",
                    "description": "Concludes the interaction",
                    "parameters": {
                        "properties": {"summary": {"type": "string"}, "is_done": {"type": "boolean"}},
                        "required": ["summary", "is_done"]
                    }
                }
            }]
        })]
        
        assert len(events) == 4
        assert isinstance(events[0], TextDeltaEvent) and events[0].content == "Hello"
        assert isinstance(events[1], TextDeltaEvent) and events[1].content == " World"
        assert isinstance(events[2], SystemToolCallEvent) and events[2].tool_name == "custom_finish_tool"
        
        # ハードコードされた固定文字列ではなく、動的コンテンツ（"Hello World"）が渡ることを検証
        assert events[2].arguments.get("summary") == "Hello World"
        assert events[2].arguments.get("is_done") is False
        assert isinstance(events[3], WorkflowFinishEvent) and events[3].finish_reason == "tool_calls"
        
        # Classifierへ正しい引数（抽出したIntent）が渡っているか確認
        args_call, _ = mock_classifier.classify.call_args
        assert "Assistant Intent: Conclude interaction" in args_call[0]

@pytest.mark.asyncio
async def test_call_llm_stream_fallback_rewrite(orchestrator):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    
    fallback_json = json.dumps({
        "id": "chatcmpl-123",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "hallucinated_tool", "arguments": "{\"text\": \"Sure!\"}"}}]
            },
            "finish_reason": "tool_calls"
        }]
    }).encode("utf-8")
    
    mock_resp.aread.return_value = fallback_json
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp
    
    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        events = [e async for e in orchestrator._call_llm("interlocutor", "http://dummy", {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{
                "type": "function", 
                "function": {
                    "name": "valid_client_tool",
                    "parameters": {"properties": {"msg": {"type": "string"}}, "required": ["msg"]}
                }
            }]
        })]
        
        assert len(events) == 2
        assert isinstance(events[0], SystemToolCallEvent) and events[0].tool_name == "valid_client_tool"
        assert events[0].arguments.get("msg") == "Sure!"
        assert isinstance(events[1], WorkflowFinishEvent)