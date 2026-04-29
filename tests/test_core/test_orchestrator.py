import pytest
import json
from pathlib import Path
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
    assert mock_gateway.call_tool.call_count >= 0
    await orchestrator.shutdown()
    mock_gateway.stop.assert_called_once()

@pytest.mark.asyncio
async def test_run_workflow_interlocutor(orchestrator):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        # 修正: null (None) が送られてきてもテストでクラッシュしないように厳密なモックを設定
        'data: {"choices": [{"delta": {"role": "assistant", "content": "Hello"}, "finish_reason": null}]}\n\n',
        'data: [DONE]\n\n'
    ]
    
    async def aiter_lines():
        for line in stream_data:
            yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    yaml_data = {"name": "interlocutor", "steps": [{"type": "llm_chat", "model_key": "interlocutor"}]}
    
    with patch("builtins.open", MagicMock()):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
                    res = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Hi"}], "stream": False})
                    assert isinstance(res, dict)
                    assert res["choices"][0]["message"]["content"] == "Hello"

@pytest.mark.asyncio
async def test_run_workflow_interlocutor_stream(orchestrator):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        'data: {"choices": [{"delta": {"content": "Streamed Hello"}, "finish_reason": null}]}\n\n',
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
                    res_gen = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Hi"}], "stream": True})
                    chunks = [c async for c in res_gen]
                    
                    assert len(chunks) == 2
                    assert "Streamed Hello" in chunks[0]["choices"][0]["delta"]["content"]

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
                        res = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Fix bug"}], "stream": False})
                        assert isinstance(res, dict)
                        assert "Task Finished" in res["choices"][0]["message"]["content"]
                        mock_agent.return_value.run.assert_called_once()

@pytest.mark.asyncio
async def test_run_workflow_agent_task_stream(orchestrator, mock_gateway):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it", "model_key": "coder"}]}

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("core.orchestrator.OpenAIServerModel"):
                    with patch("core.orchestrator.ToolCallingAgent") as mock_agent:
                        mock_agent.return_value.run.return_value = "Task Finished"
                        res_gen = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Fix bug"}], "stream": True})
                        chunks = [c async for c in res_gen]
                        assert len(chunks) > 0
                        assert any("Task Finished" in c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)

@pytest.mark.asyncio
async def test_workflow_missing_model_key(orchestrator):
    orchestrator.router.route = AsyncMock(return_value="coder")
    yaml_data = {"name": "coder", "steps": [{"type": "agent_task", "description": "fix it"}]}

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                res = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Fix bug"}], "stream": False})
                assert isinstance(res, dict)
                assert "ERROR" in res["choices"][0]["message"]["content"]

@pytest.mark.asyncio
async def test_workflow_file_not_found(orchestrator):
    with patch("pathlib.Path.exists", return_value=False):
        res = await orchestrator.orchestrate({"messages": [{"role": "user", "content": "Hi"}], "stream": False})
        assert isinstance(res, dict)
        assert "ERROR" in res["choices"][0]["message"]["content"]

@pytest.mark.asyncio
async def test_submit_chat_completion_proxies_to_llm(orchestrator):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        'data: {"choices": [{"delta": {"role": "assistant", "content": "Response"}}]}\n\n',
        'data: [DONE]\n\n'
    ]
    async def aiter_lines():
        for line in stream_data: yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        orchestrator.router.route = AsyncMock(return_value="interlocutor")
        yaml_data = {"steps": [{"type": "llm_chat", "model_key": "interlocutor"}]}
        with patch("builtins.open", MagicMock()):
            with patch("yaml.safe_load", return_value=yaml_data):
                with patch("pathlib.Path.exists", return_value=True):
                    res = await orchestrator.submit_chat_completion({
                        "messages": [{"role": "user", "content": "Hi"}],
                        "tools": [{"type": "function", "function": {"name": "some_tools"}}], 
                        "stream": False
                    })
                    assert isinstance(res, dict)
                    assert res["choices"][0]["message"]["content"] == "Response"

@pytest.mark.asyncio
async def test_call_llm_system_prompt_merge(orchestrator):
    mock_stream_resp = AsyncMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-type": "text/event-stream"}
    async def aiter_lines():
        for line in []: yield line
    mock_stream_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_stream_resp
    
    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx) as mock_stream:
        gen = await orchestrator._call_llm("interlocutor", "http://dummy", {
            "messages": [{"role": "system", "content": "Roo Custom System"}, {"role": "user", "content": "Hi"}],
            "stream": True
        })
        [c async for c in gen]
        
        call_kwargs = mock_stream.call_args[1]
        sent_messages = call_kwargs["json"]["messages"]
        assert len(sent_messages) == 2
        assert "You are BROWNIE." in sent_messages[0]["content"]
        assert "Roo Custom System" in sent_messages[0]["content"]

@pytest.mark.asyncio
async def test_call_llm_injects_max_tokens(orchestrator):
    mock_stream_resp = AsyncMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-type": "text/event-stream"}
    async def aiter_lines():
        for line in []: yield line
    mock_stream_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_stream_resp
    
    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx) as mock_stream:
        gen = await orchestrator._call_llm("interlocutor", "http://dummy", {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True
        })
        [c async for c in gen]
        
        call_kwargs = mock_stream.call_args[1]
        assert call_kwargs["json"]["max_tokens"] == 8192

# --- 反芻(Reflection)アーキテクチャのテスト ---
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
        for line in stream_data:
            yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp
    
    mock_ref_resp = MagicMock(status_code=200)
    mock_ref_resp.json.return_value = {"choices": [{"message": {"content": "custom_finish_tool"}}]}

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        with patch("httpx.AsyncClient.post", return_value=mock_ref_resp):
            gen = await orchestrator._call_llm("interlocutor", "http://dummy", {
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [{
                    "type": "function", 
                    "function": {
                        "name": "custom_finish_tool",
                        "parameters": {
                            "properties": {"summary": {"type": "string"}, "is_done": {"type": "boolean"}},
                            "required": ["summary", "is_done"]
                        }
                    }
                }],
                "stream": True
            })
            
            chunks = [c async for c in gen]
            
            assert chunks[0]["choices"][0]["delta"]["content"] == "Hello"
            assert chunks[1]["choices"][0]["delta"]["content"] == " World"
            assert chunks[2]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "custom_finish_tool"
            args = json.loads(chunks[2]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"])
            assert args["summary"] == "Response provided in chat."
            assert args["is_done"] is False
            assert chunks[3]["choices"][0]["finish_reason"] == "tool_calls"


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
        gen = await orchestrator._call_llm("interlocutor", "http://dummy", {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{
                "type": "function", 
                "function": {
                    "name": "valid_client_tool",
                    "parameters": {"properties": {"msg": {"type": "string"}}, "required": ["msg"]}
                }
            }],
            "stream": True
        })
        
        chunks = [c async for c in gen]
        
        tc = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["function"]["name"] == "valid_client_tool"
        args = json.loads(tc["function"]["arguments"])
        assert args["msg"] == "Sure!"

# === GatewayClient Tests ===
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
    mock_path = MagicMock()
    mock_path.glob.return_value = [Path("coder.yaml"), Path("interlocutor.yaml"), Path("painter.yaml")]
    return Router(settings, mock_path)

@pytest.mark.asyncio
async def test_route_empty_query(router):
    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": "interlocutor"}}]}
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route([{"role": "user", "content": ""}]) == "interlocutor"

@pytest.mark.asyncio
async def test_route_llm_fallback_interlocutor(router):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "interlocutor"}}]
    }
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route([{"role": "user", "content": "こんにちは、調子はどう？"}]) == "interlocutor"

@pytest.mark.asyncio
async def test_route_llm_fallback_coder(router):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "coder"}}]
    }
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route([{"role": "user", "content": "このアルゴリズムを修正して"}]) == "coder"

@pytest.mark.asyncio
async def test_route_llm_future_actor(router):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "painter"}}]
    }
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        assert await router.route([{"role": "user", "content": "絵を描いて"}]) == "painter"

@pytest.mark.asyncio
@patch("core.orchestrator.logger.error")
async def test_route_llm_error_fallback(mock_logger_error, router):
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection Timeout")):
        assert await router.route([{"role": "user", "content": "複雑な要求"}]) == "interlocutor"
        mock_logger_error.assert_called_once()