# tests/test_api/test_server.py
import pytest
import json
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

def test_health_check(test_client):
    client, _ = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "engine_ready": True}

def test_chat_completions(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield TextDeltaEvent(content="Hi there!")
        yield WorkflowFinishEvent(finish_reason="stop")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hi there!"

def test_chat_completions_list_content(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield TextDeltaEvent(content="Parsed fine!")
        yield WorkflowFinishEvent(finish_reason="stop")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
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
    assert call_args["messages"][0]["content"] == "Part 1\nPart 2"

def test_chat_completions_tool_calls(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield ToolCallStartEvent(index=0, id="call_123", tool_name="my_tool")
        yield ToolCallDeltaEvent(index=0, arguments="{}")
        yield WorkflowFinishEvent(finish_reason="tool_calls")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use tool"}],
        "stream": False
    })
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["choices"][0]["message"]["content"] is None
    assert resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "my_tool"

def test_chat_completions_system_tool_calls(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield SystemToolCallEvent(index=0, id="call_sys", tool_name="sys_tool", arguments={"key": "val"})
        yield WorkflowFinishEvent(finish_reason="tool_calls")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use system tool"}],
        "stream": False
    })
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "sys_tool"
    assert "val" in resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]

def test_chat_completions_stream(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield TextDeltaEvent(content="Hi there from stream!")
        yield WorkflowFinishEvent(finish_reason="stop")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
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
    
    # 最初のチャンクに role: assistant が含まれることの確認
    assert data_chunk1["choices"][0]["delta"]["role"] == "assistant"
    assert data_chunk1["choices"][0]["delta"]["content"] == "Hi there from stream!"
    assert data_chunk2["choices"][0]["finish_reason"] == "stop"
    assert data_done == "data: [DONE]"

def test_chat_completions_system_tool_calls_stream(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield SystemToolCallEvent(index=0, id="call_sys", tool_name="sys_tool", arguments={"key": "val"})
        yield WorkflowFinishEvent(finish_reason="tool_calls")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use system tool"}],
        "stream": True
    })
    
    assert response.status_code == 200
    
    content = response.text
    lines = content.strip().split("\n\n")
    assert len(lines) == 3
    
    data_chunk1 = json.loads(lines[0].replace("data: ", ""))
    data_chunk2 = json.loads(lines[1].replace("data: ", ""))
    data_done = lines[2]
    
    # 最初のチャンクに role: assistant が含まれることの確認
    assert data_chunk1["choices"][0]["delta"]["role"] == "assistant"
    
    # 1つのチャンクで tool_calls が飛び、arguments が JSON文字列として組み立てられていることの確認
    tc = data_chunk1["choices"][0]["delta"]["tool_calls"][0]
    assert tc["id"] == "call_sys"
    assert tc["function"]["name"] == "sys_tool"
    assert json.loads(tc["function"]["arguments"]) == {"key": "val"}
    
    assert data_chunk2["choices"][0]["finish_reason"] == "tool_calls"
    assert data_done == "data: [DONE]"

def test_chat_completions_error_response(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield ErrorEvent(message="something went wrong")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
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

def test_chat_completions_proxies_full_request(test_client):
    client, mock_orch = test_client
    
    async def mock_workflow(*args, **kwargs):
        yield TextDeltaEvent(content="OK")
        
    mock_orch.process_workflow.side_effect = mock_workflow
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"type": "function", "function": {"name": "test", "description": "test tool"}}]
    })
    
    assert response.status_code == 200
    sent_data = mock_orch.process_workflow.call_args[0][0]
    assert "tools" in sent_data
    assert sent_data["tools"][0]["function"]["name"] == "test"