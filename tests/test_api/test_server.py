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