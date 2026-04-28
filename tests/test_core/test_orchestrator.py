import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator, Settings, Router, GatewayClient
import mcp.types as types

# === Orchestrator Tests ===
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
async def test_run_workflow_interlocutor(orchestrator):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "Hello"}}]}

    yaml_data = {"name": "interlocutor", "steps": [{"type": "llm_chat", "model_key": "interlocutor"}]}
    
    with patch("builtins.open", MagicMock()):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("httpx.AsyncClient.post", return_value=mock_resp):
                    res = await orchestrator.orchestrate([{"role": "user", "content": "Hi"}])
                    assert "Hello" in res["choices"][0]["message"]["content"]

@pytest.mark.asyncio
async def test_run_workflow_agent_task(orchestrator, mock_gateway):
    orchestrator.router.route = AsyncMock(return_value="coder")
    
    yaml_data = {
        "name": "coder",
        "steps": [{"type": "agent_task", "description": "fix it", "allowed_tools": ["test_tool"], "model_key": "coder"}]
    }

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("core.orchestrator.OpenAIServerModel"):
                    with patch("core.orchestrator.ToolCallingAgent") as mock_agent:
                        mock_agent.return_value.run.return_value = "Task Finished"
                        
                        res = await orchestrator.orchestrate([{"role": "user", "content": "Fix bug"}])
                        
                        assert "Task Finished" in res["choices"][0]["message"]["content"]
                        mock_agent.return_value.run.assert_called_once()

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    orchestrator.router.route = AsyncMock(return_value="coder")
    
    yaml_data = {
        "name": "coder",
        "steps": [{"type": "agent_task", "description": "fix it"}] # model_key が無い
    }

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                res = await orchestrator.orchestrate([{"role": "user", "content": "Fix bug"}])
                assert "ERROR" in res["choices"][0]["message"]["content"]
                assert "missing required 'model_key'" in res["choices"][0]["message"]["content"]

@pytest.mark.asyncio
async def test_workflow_file_not_found(orchestrator):
    with patch("pathlib.Path.exists", return_value=False):
        res = await orchestrator.orchestrate([{"role": "user", "content": "Hi"}])
        assert "ERROR" in res["choices"][0]["message"]["content"]

# === GatewayClient Tests (Merged from test_config.py/gateway.client tests) ===
@pytest.fixture
def client():
    return GatewayClient()

@pytest.mark.asyncio
async def test_gateway_start_stop(client):
    mock_stdio = AsyncMock()
    mock_stdio.__aenter__.return_value = (AsyncMock(), AsyncMock())
    mock_stdio.__aexit__ = AsyncMock()
    
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__ = AsyncMock()

    # モックの対象を gateway.client ではなく core.orchestrator に修正
    with patch("core.orchestrator.stdio_client", return_value=mock_stdio):
        with patch("core.orchestrator.ClientSession", return_value=mock_session_ctx):
            await client.start()
            assert client.session is not None
            mock_session.initialize.assert_called_once()
            
            await client.stop()
            assert client.session is None

@pytest.mark.asyncio
async def test_gateway_fetch_tools(client):
    mock_session = AsyncMock()
    mock_tool = MagicMock()
    mock_tool.name = "mock_tool"
    mock_tool.description = "desc"
    mock_tool.inputSchema = {}
    
    mock_result = MagicMock()
    mock_result.tools = [mock_tool]
    mock_session.list_tools = AsyncMock(return_value=mock_result)
    
    client.session = mock_session
    tools = await client.fetch_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "mock_tool"

@pytest.mark.asyncio
async def test_gateway_call_tool(client):
    mock_session = AsyncMock()
    mock_content = types.TextContent(type="text", text="Output")
    mock_result = MagicMock()
    mock_result.content = [mock_content]
    mock_session.call_tool = AsyncMock(return_value=mock_result)
    
    client.session = mock_session
    res = await client.call_tool("mock_tool", {"param": 1})
    assert res == "Output"
    mock_session.call_tool.assert_called_once_with("mock_tool", {"param": 1})

@pytest.mark.asyncio
async def test_gateway_call_tool_not_connected(client):
    client.session = None
    with pytest.raises(ValueError):
        await client.call_tool("mock_tool", {})

# === Router Tests ===
@pytest.fixture
def router():
    settings = Settings()
    return Router(settings)

@pytest.mark.asyncio
async def test_route_empty_query(router):
    """空のクエリは即座に interlocutor になる"""
    assert await router.route("") == "interlocutor"

@pytest.mark.asyncio
async def test_route_heuristic_coder(router):
    """キーワードが含まれる場合は LLM を呼ばずに coder を返す"""
    with patch("httpx.AsyncClient.post") as mock_post:
        assert await router.route("Pythonのコードを修正して") == "coder"
        assert await router.route("バグがあるみたい") == "coder"
        mock_post.assert_not_called()

@pytest.mark.asyncio
async def test_route_llm_fallback_interlocutor(router):
    """キーワードがなく、LLM が interlocutor と判定した場合"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "interlocutor"}}]
    }
    
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route("こんにちは、調子はどう？") == "interlocutor"

@pytest.mark.asyncio
async def test_route_llm_fallback_coder(router):
    """キーワードはないが、LLMの文脈理解で coder と判定した場合"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "coder"}}]
    }
    
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route("このアルゴリズムをもっと速くできないかな？") == "coder"

@pytest.mark.asyncio
@patch("core.orchestrator.logger.error")
async def test_route_llm_error_fallback(mock_logger_error, router):
    """LLM呼び出しでエラーが発生した場合は安全に interlocutor にフォールバックする"""
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection Timeout")):
        assert await router.route("複雑な要求") == "interlocutor"
        mock_logger_error.assert_called_once()
        assert "Router LLM Error" in mock_logger_error.call_args[0][0]