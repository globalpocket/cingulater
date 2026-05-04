# tests/test_core/test_orchestrator.py
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator, Settings, Router, MCPClient
from core.events import TextDeltaEvent, ToolCallStartEvent, ToolCallDeltaEvent, SystemToolCallEvent, WorkflowFinishEvent, ErrorEvent
from core.schema import InternalAgentRequest, InternalMessage, InternalTool
from core.llm_client import StandardLLMChunk, ToolCallChunk
import mcp.types as types

@pytest.mark.asyncio
async def test_router_route():
    settings = Settings()
    
    with patch("pathlib.Path.glob") as mock_glob, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load") as mock_yaml_load:
         
        mock_path1 = MagicMock()
        mock_path1.stem = "coder"
        mock_glob.return_value = [mock_path1]
        
        mock_yaml_load.side_effect = [
            {"name": "interlocutor", "description": "Chat with user", "steps": [{"type": "llm_chat"}]},
            {"name": "coder", "description": "Write code", "steps": [{"type": "llm_chat"}]}
        ]
        
        mock_orch = MagicMock()
        mock_orch.project_root = Path("dummy_root")
        mock_orch._extract_intent = AsyncMock(return_value="Chat with user")
        
        # mcp-reranker のクライアントをモック
        mock_reranker_client = AsyncMock()
        mock_reranker_client.call_tool.return_value = json.dumps([
            {"document": "Chat with user", "score": 0.9},
            {"document": "Write code", "score": 0.1}
        ])
        mock_orch.mcp_clients = {"mcp-reranker": mock_reranker_client}
        
        router = Router(settings, Path("dummy"), orchestrator=mock_orch)
        
        messages = [InternalMessage(role="user", content="Hello")]
        selected_actor, steps = await router.route(messages)
        
        assert selected_actor == "interlocutor"
        assert steps == [{"type": "llm_chat"}]
        mock_orch._extract_intent.assert_called_once()
        
        # rerank_documents ツールが正しく呼ばれたか検証
        mock_reranker_client.call_tool.assert_called_once_with(
            "rerank_documents",
            {"query": "Chat with user", "documents": ["Chat with user", "Write code"]}
        )

@pytest.fixture
def mock_mcp_client():
    with patch("core.orchestrator.MCPClient") as mock:
        inst = mock.return_value
        inst.start = AsyncMock()
        inst.stop = AsyncMock()
        inst.fetch_tools = AsyncMock(return_value=[{"name": "test_tool", "description": "desc", "inputSchema": {}}])
        inst.call_tool = AsyncMock(return_value="Success")
        yield inst

@pytest.fixture
def orchestrator(mock_mcp_client):
    with patch("pathlib.Path.exists", return_value=False):
        o = Orchestrator("dummy.yaml")
        # 戻り値をタプル (actor, steps) に変更
        o.router.route = AsyncMock(return_value=("interlocutor", []))
        # テストのためにダミーの mlx-launcher クライアントを注入する
        o.mcp_clients["mlx-launcher"] = mock_mcp_client
        return o

@pytest.mark.asyncio
async def test_start_shutdown(orchestrator, mock_mcp_client):
    # Auto-launch の動的抽象化テストのために設定を注入
    orchestrator.settings.llm.models = {"interlocutor": "dummy-model"}
    orchestrator.settings.llm.launcher_client = "mlx-launcher"
    orchestrator.settings.llm.launcher_tool = "launch_llm_server"

    await orchestrator.start()
    
    assert mock_mcp_client.start.call_count >= 1
    # 指定したツール名とパラメータで呼び出されているか確認
    mock_mcp_client.call_tool.assert_called_with("launch_llm_server", {"model_name": "dummy-model", "port": 8080})
    
    await orchestrator.shutdown()
    assert mock_mcp_client.stop.call_count >= 1

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
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(content="Hello")
        yield StandardLLMChunk(finish_reason="stop")
        
    orchestrator.llm_client.stream_chat = mock_stream_chat

    # Routerからの戻り値をモック化
    orchestrator.router.route = AsyncMock(return_value=(
        "interlocutor", [{"type": "llm_chat", "model_key": "interlocutor"}]
    ))
    
    req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Hi")])
    events = [e async for e in orchestrator.process_workflow(req)]
    
    assert len(events) == 2
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].content == "Hello"
    assert isinstance(events[1], WorkflowFinishEvent)
    assert events[1].finish_reason == "stop"

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    orchestrator.router.route = AsyncMock(return_value=(
        "coder", [{"type": "llm_chat", "description": "fix it"}]
    ))

    req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Fix bug")])
    events = [e async for e in orchestrator.process_workflow(req)]
    
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)

@pytest.mark.asyncio
async def test_workflow_file_not_found(orchestrator):
    # fixture で empty な steps ("interlocutor", []) が返る設定になっているためエラーになる
    req = InternalAgentRequest(messages=[InternalMessage(role="user", content="Hi")])
    events = [e async for e in orchestrator.process_workflow(req)]
    
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert "empty or missing" in events[0].message

@pytest.mark.asyncio
async def test_run_workflow_with_reflection(orchestrator):
    """ツール呼び出しがなく、テキストのみが返された場合のリフレクション(Reranker)のテスト"""
    
    async def mock_stream_chat(*args, **kwargs):
        yield StandardLLMChunk(content="Hello")
        yield StandardLLMChunk(content=" World")
        yield StandardLLMChunk(finish_reason="stop")
        
    orchestrator.llm_client.stream_chat = mock_stream_chat
    
    orchestrator.router.route = AsyncMock(return_value=(
        "interlocutor", [{"type": "llm_chat", "model_key": "interlocutor"}]
    ))
    orchestrator._extract_intent = AsyncMock(return_value="Conclude interaction")
    
    # mcp-reranker のクライアントをモック
    mock_reranker_client = AsyncMock()
    mock_reranker_client.call_tool.return_value = json.dumps([
        {"document": "Concludes the interaction", "score": 0.99}
    ])
    orchestrator.mcp_clients["mcp-reranker"] = mock_reranker_client

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

    events = [e async for e in orchestrator.process_workflow(req)]
    
    assert len(events) == 4
    assert isinstance(events[0], TextDeltaEvent) and events[0].content == "Hello"
    assert isinstance(events[1], TextDeltaEvent) and events[1].content == " World"
    # リフレクションによってツール呼び出しが自動生成される
    assert isinstance(events[2], SystemToolCallEvent) and events[2].tool_name == "custom_finish_tool"
    
    assert events[2].arguments.get("summary") == "Hello World"
    assert events[2].arguments.get("is_done") is False
    assert isinstance(events[3], WorkflowFinishEvent) and events[3].finish_reason == "tool_calls"
    
    # MCPツール呼び出しを検証
    mock_reranker_client.call_tool.assert_called_once_with(
        "rerank_documents",
        {"query": "Conclude interaction", "documents": ["Concludes the interaction"]}
    )

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