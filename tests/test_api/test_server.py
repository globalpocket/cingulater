# tests/test_api/test_server.py
import pytest
import json
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from core.events import TextDeltaEvent, ToolCallStartEvent, ToolCallDeltaEvent, SystemToolCallEvent, WorkflowFinishEvent, ErrorEvent

@pytest.fixture
def test_client():
    with patch("api.server.Orchestrator") as mock_orch_cls:
        mock_orch = AsyncMock()
        mock_orch.process_workflow = MagicMock()
        mock_orch_cls.return_value = mock_orch
        
        from api.server import app
        with TestClient(app) as client:
            yield client, mock_orch

@pytest.fixture
def mock_workflow_factory():
    """指定されたイベントのリストを順番にyieldする非同期ジェネレータのファクトリ関数"""
    def _factory(events):
        async def _workflow(*args, **kwargs):
            for event in events:
                yield event
        return _workflow
    return _factory


def test_health_check(test_client):
    client, _ = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "engine_ready": True}

def test_chat_completions(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="Hi there!"),
        WorkflowFinishEvent(finish_reason="stop")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hi there!"

def test_chat_completions_list_content(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="Parsed fine!"),
        WorkflowFinishEvent(finish_reason="stop")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [
            {
                "role": "user", 
                "content": [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]
            }
        ],
        "stream": False
    })
    
    assert response.status_code == 200
    call_args = mock_orch.process_workflow.call_args[0][0]
    assert call_args.messages[0].content == "Part 1\nPart 2"

def test_chat_completions_tool_calls(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        ToolCallStartEvent(index=0, id="call_123", tool_name="my_tool"),
        ToolCallDeltaEvent(index=0, arguments="{}"),
        WorkflowFinishEvent(finish_reason="tool_calls")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use tool"}],
        "stream": False
    })
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["choices"][0]["message"]["content"] is None
    assert resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "my_tool"

def test_chat_completions_system_tool_calls(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        SystemToolCallEvent(index=0, id="call_sys_short", tool_name="sys_tool", arguments={"key": "val"}),
        WorkflowFinishEvent(finish_reason="tool_calls")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use system tool"}],
        "stream": False
    })
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "sys_tool"
    assert "val" in resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    # サーバー側でIDが補完されていることを確認
    assert len(resp_json["choices"][0]["message"]["tool_calls"][0]["id"]) >= 20

def test_chat_completions_stream(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="Hi there from stream!"),
        WorkflowFinishEvent(finish_reason="stop")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True
    })
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    
    content = response.text
    lines = content.strip().split("\n\n")
    assert len(lines) == 3
    
    data_chunk1 = json.loads(lines[0].replace("data: ", ""))
    data_chunk2 = json.loads(lines[1].replace("data: ", ""))
    data_done = lines[2]
    
    assert data_chunk1["choices"][0]["delta"]["role"] == "assistant"
    assert data_chunk1["choices"][0]["delta"]["content"] == "Hi there from stream!"
    assert data_chunk2["choices"][0]["finish_reason"] == "stop"
    assert data_done == "data: [DONE]"

def test_chat_completions_system_tool_calls_stream(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        SystemToolCallEvent(index=0, id="call_sys", tool_name="sys_tool", arguments={"key": "val"}),
        WorkflowFinishEvent(finish_reason="tool_calls")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use system tool"}],
        "stream": True
    })
    
    assert response.status_code == 200
    
    content = response.text
    lines = content.strip().split("\n\n")
    # Start chunk, Delta chunk, Finish chunk, DONE
    assert len(lines) == 4
    
    data_chunk1 = json.loads(lines[0].replace("data: ", ""))
    data_chunk2 = json.loads(lines[1].replace("data: ", ""))
    data_chunk3 = json.loads(lines[2].replace("data: ", ""))
    data_done = lines[3]
    
    # 最初のチャンクは role: assistant と name を含む
    assert data_chunk1["choices"][0]["delta"]["role"] == "assistant"
    tc1 = data_chunk1["choices"][0]["delta"]["tool_calls"][0]
    assert tc1["id"].startswith("call_")
    assert len(tc1["id"]) >= 20  # サーバー側でID補完されているか
    assert tc1["function"]["name"] == "sys_tool"
    assert tc1["function"]["arguments"] == ""
    
    # 2つ目のチャンクは arguments を含む
    tc2 = data_chunk2["choices"][0]["delta"]["tool_calls"][0]
    assert json.loads(tc2["function"]["arguments"]) == {"key": "val"}
    
    assert data_chunk3["choices"][0]["finish_reason"] == "tool_calls"
    assert data_done == "data: [DONE]"

def test_chat_completions_error_response(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        ErrorEvent(message="something went wrong")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 200
    assert "ERROR: something went wrong" in response.json()["choices"][0]["message"]["content"]
    assert response.json()["choices"][0]["finish_reason"] == "error"

def test_chat_completions_validation_error(test_client):
    client, _ = test_client
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": "invalid_messages_format", 
        "stream": False
    })
    
    assert response.status_code == 422

def test_chat_completions_proxies_full_request(test_client, mock_workflow_factory):
    client, mock_orch = test_client
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="OK")
    ])
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"type": "function", "function": {"name": "test", "description": "test tool"}}]
    })
    
    assert response.status_code == 200
    call_args = mock_orch.process_workflow.call_args[0][0]
    assert call_args.tools is not None
    assert call_args.tools[0].function["name"] == "test"

@pytest.mark.asyncio
async def test_chat_completions_stream_keep_alive(test_client):
    """LLMの応答が遅延した際に keep-alive コメントが出力されるか検証"""
    client, mock_orch = test_client
    
    async def delayed_workflow(*args, **kwargs):
        yield TextDeltaEvent(content="Start...")
        await asyncio.sleep(0.05) # Keep-Alive間隔を超えるよう少しだけ待機
        yield TextDeltaEvent(content="Delayed...")
        yield WorkflowFinishEvent(finish_reason="stop")

    mock_orch.process_workflow.side_effect = delayed_workflow

    # サーバーの定数 KEEP_ALIVE_INTERVAL を極端に短くしてテスト
    with patch("api.server.KEEP_ALIVE_INTERVAL", 0.01):
        response = client.post("/v1/chat/completions", json={
            "model": "brownie-v2",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        
        assert response.status_code == 200
        # keep-alive が出力に挟まっているか確認
        assert ": keep-alive" in response.text
        assert "Start..." in response.text
        assert "Delayed..." in response.text


@pytest.mark.asyncio
async def test_chat_completions_single_task_mode(test_client, mock_workflow_factory):
    """single_task_mode が有効な場合、ロックが取得されるか検証"""
    client, mock_orch = test_client
    
    mock_orch.settings.agent.single_task_mode = True
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="Single task mode response"),
        WorkflowFinishEvent(finish_reason="stop")
    ])
    
    with patch("api.server.chat_lock", new_callable=AsyncMock) as mock_lock:
        response = client.post("/v1/chat/completions", json={
            "model": "brownie-v2",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False
        })
        
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Single task mode response"
        # ロックが取得・解放されたことを確認
        assert mock_lock.__aenter__.called
        assert mock_lock.__aexit__.called


@pytest.mark.asyncio
async def test_chat_completions_stream_single_task_mode(test_client, mock_workflow_factory):
    """single_task_mode が有効な場合 (ストリーミング時)、ロックが取得されるか検証"""
    client, mock_orch = test_client
    
    mock_orch.settings.agent.single_task_mode = True
    
    mock_orch.process_workflow.side_effect = mock_workflow_factory([
        TextDeltaEvent(content="Single task mode stream"),
        WorkflowFinishEvent(finish_reason="stop")
    ])
    
    with patch("api.server.chat_lock", new_callable=AsyncMock) as mock_lock:
        response = client.post("/v1/chat/completions", json={
            "model": "brownie-v2",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        
        assert response.status_code == 200
        assert "Single task mode stream" in response.text
        # ロックが取得・解放されたことを確認
        assert mock_lock.__aenter__.called
        assert mock_lock.__aexit__.called