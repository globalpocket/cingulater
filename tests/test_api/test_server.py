import pytest
import json
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

@pytest.fixture
def test_client():
    with patch("api.server.Orchestrator") as mock_orch_cls:
        mock_orch = AsyncMock()
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
    
    mock_orch.submit_chat_completion.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Hi there!"}, "finish_reason": "stop"}]
    }
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hi there!"

def test_chat_completions_list_content(test_client):
    client, mock_orch = test_client
    
    mock_orch.submit_chat_completion.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Parsed fine!"}, "finish_reason": "stop"}]
    }
    
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
    call_args = mock_orch.submit_chat_completion.call_args[0][0]
    # messagesの構造が辞書で渡されるため、適切にアクセスして検証
    assert call_args["messages"][0]["content"] == "Part 1\nPart 2"

def test_chat_completions_tool_calls(test_client):
    client, mock_orch = test_client
    
    mock_orch.submit_chat_completion.return_value = {
        "choices": [{
            "message": {
                "role": "assistant", 
                "content": None,
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "my_tool", "arguments": "{}"}
                }]
            },
            "finish_reason": "tool_calls"
        }]
    }
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Use tool"}],
        "stream": False
    })
    
    assert response.status_code == 200
    resp_json = response.json()
    assert resp_json["choices"][0]["message"]["content"] is None
    assert resp_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "my_tool"

def test_chat_completions_stream(test_client):
    client, mock_orch = test_client
    
    async def mock_stream():
        yield {
            "id": "chatcmpl-123",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi there from stream!"}, "finish_reason": "stop"}]
        }
        
    mock_orch.submit_chat_completion.return_value = mock_stream()
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True
    })
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    
    content = response.text
    lines = content.strip().split("\n\n")
    assert len(lines) == 2
    
    data_chunk = lines[0].replace("data: ", "")
    data_done = lines[1]
    
    chunk_json = json.loads(data_chunk)
    assert chunk_json["choices"][0]["delta"]["content"] == "Hi there from stream!"
    assert data_done == "data: [DONE]"

def test_chat_completions_error_response(test_client):
    client, mock_orch = test_client
    
    mock_orch.submit_chat_completion.return_value = {"error": "something went wrong"}
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 500
    assert "Invalid response" in response.json()["detail"]

def test_chat_completions_validation_error(test_client):
    client, _ = test_client
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": "invalid_messages_format", 
        "stream": False
    })
    
    assert response.status_code == 422
    resp_json = response.json()
    assert "detail" in resp_json
    assert "body" in resp_json
    assert "invalid_messages_format" in resp_json["body"]

def test_chat_completions_proxies_full_request(test_client):
    client, mock_orch = test_client
    mock_orch.submit_chat_completion.return_value = {"choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]}
    
    # toolsを含むリクエストを送信
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"type": "function", "function": {"name": "test", "description": "test tool"}}]
    })
    
    assert response.status_code == 200
    # Orchestratorに渡されたデータにtoolsが含まれているか確認
    sent_data = mock_orch.submit_chat_completion.call_args[0][0]
    assert "tools" in sent_data
    assert sent_data["tools"][0]["function"]["name"] == "test"