import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator

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
    """interlocutor(llm_chat)の正常系"""
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
    """coder(agent_task)の正常系。smolagents の動作をモック"""
    orchestrator.router.route = AsyncMock(return_value="coder")
    
    yaml_data = {
        "name": "coder",
        "steps": [{"type": "agent_task", "description": "fix it", "allowed_tools": ["test_tool"], "model_key": "coder"}]
    }

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                # OpenAIServerModel のインスタンス化をモックして依存エラーを回避
                with patch("core.orchestrator.OpenAIServerModel"):
                    with patch("core.orchestrator.ToolCallingAgent") as mock_agent:
                        mock_agent.return_value.run.return_value = "Task Finished"
                        
                        res = await orchestrator.orchestrate([{"role": "user", "content": "Fix bug"}])
                        
                        assert "Task Finished" in res["choices"][0]["message"]["content"]
                        mock_agent.return_value.run.assert_called_once()

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    """YAMLのステップに必須である model_key が欠損している場合のエラー処理"""
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