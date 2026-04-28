import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from core.orchestrator import Orchestrator

@pytest.fixture
def mock_gateway():
    with patch("core.orchestrator.GatewayClient") as mock:
        # GatewayClientの各非同期メソッドを AsyncMock に明示的に設定
        mock_instance = mock.return_value
        mock_instance.start = AsyncMock()
        mock_instance.stop = AsyncMock()
        mock_instance.fetch_tools = AsyncMock(return_value=[{"name": "test_tool"}])
        mock_instance.call_tool = AsyncMock(return_value="Tool Executed")
        yield mock

@pytest.fixture
def orchestrator(mock_gateway):
    """MCPとLLMの実際の接続を行わないOrchestratorインスタンス"""
    with patch("pathlib.Path.exists", return_value=False):
        o = Orchestrator("dummy.yaml")
        # Router をモック化
        o.router.route = AsyncMock(return_value="interlocutor")
        return o

@pytest.mark.asyncio
async def test_start_shutdown(orchestrator):
    await orchestrator.start()
    orchestrator.mcp_client.start.assert_called_once()
    
    await orchestrator.shutdown()
    orchestrator.mcp_client.stop.assert_called_once()

@pytest.mark.asyncio
async def test_system_prompt_loading_success():
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pathlib.Path.read_text", return_value="Custom Prompt"):
            with patch("core.orchestrator.GatewayClient"):
                o = Orchestrator("dummy.yaml")
                assert o.system_prompt == "Custom Prompt"

@pytest.mark.asyncio
async def test_orchestrate_interlocutor(orchestrator):
    """対話モード(interlocutor)の正常系テスト"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "Hello!"}}]}
    
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_resp
    mock_client_instance.__aenter__.return_value = mock_client_instance

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        res = await orchestrator.submit_chat_completion([{"role": "user", "content": "Hi"}])
        assert res is not None
        assert res["choices"][0]["message"]["content"] == "Hello!"

@pytest.mark.asyncio
async def test_orchestrate_coder_success(orchestrator):
    """コーダーモード(coder)での、計画立案→ツール実行→完了報告のループテスト"""
    orchestrator.router.route = AsyncMock(return_value="coder")

    # UIクラッシュバグ回避のためバッククォートを変数化
    bq = "`" * 3
    
    # 1. 計画立案のレスポンス
    mock_resp_plan = MagicMock()
    mock_resp_plan.status_code = 200
    mock_resp_plan.json.return_value = {
        "choices": [{"message": {"content": f"{bq}yaml\nplan:\n  - step: 1\n    description: 'do stuff'\n{bq}"}}]
    }

    # 2. ツール呼び出しのレスポンス
    mock_resp_tool = MagicMock()
    mock_resp_tool.status_code = 200
    mock_resp_tool.json.return_value = {
        "choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "test_tool", "arguments": "{}"}}]}}]
    }

    # 3. 完了のレスポンス
    mock_resp_final = MagicMock()
    mock_resp_final.status_code = 200
    mock_resp_final.json.return_value = {
        "choices": [{"message": {"content": "Task Done!"}}]
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = [mock_resp_plan, mock_resp_tool, mock_resp_final]
    mock_client_instance.__aenter__.return_value = mock_client_instance

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        res = await orchestrator.submit_chat_completion([{"role": "user", "content": "Fix bug"}])
        assert res is not None
        content = res["choices"][0]["message"]["content"]
        assert "すべての計画ステップ" in content
        assert "Task Done!" in content
        orchestrator.mcp_client.call_tool.assert_called_once_with("test_tool", {})

@pytest.mark.asyncio
async def test_orchestrate_coder_yaml_fail(orchestrator):
    """YAML計画が正しく出力されなかった場合のエラーハンドリング"""
    orchestrator.router.route = AsyncMock(return_value="coder")
    
    mock_resp_plan = MagicMock()
    mock_resp_plan.status_code = 200
    mock_resp_plan.json.return_value = {
        "choices": [{"message": {"content": "No yaml format here."}}]
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_resp_plan
    mock_client_instance.__aenter__.return_value = mock_client_instance

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        res = await orchestrator.submit_chat_completion([{"role": "user", "content": "Fix bug"}])
        assert res is not None
        assert "ERROR" in res["choices"][0]["message"]["content"]

@pytest.mark.asyncio
async def test_call_llm_http_error(orchestrator):
    """HTTPエラー(Connection Error)のフォールバック"""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = Exception("Network Timeout")
    mock_client_instance.__aenter__.return_value = mock_client_instance

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        res = await orchestrator.submit_chat_completion([{"role": "user", "content": "Hi"}])
        assert res is not None
        assert "ERROR" in res["choices"][0]["message"]["content"]