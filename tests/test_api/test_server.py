import pytest
import json
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

@pytest.fixture
def test_client():
    # Lifespan イベント中に走る Orchestrator 初期化をモック化
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
    
    # 正常系のLLMレスポンスをモック
    mock_orch.submit_chat_completion.return_value = {
        "choices": [{"message": {"content": "Hi there!"}}]
    }
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    })
    
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hi there!"

def test_chat_completions_stream(test_client):
    client, mock_orch = test_client
    
    # ストリーミング要求に対しても、内部的には同じように一括結果を返す
    mock_orch.submit_chat_completion.return_value = {
        "choices": [{"message": {"content": "Hi there from stream!"}}]
    }
    
    response = client.post("/v1/chat/completions", json={
        "model": "brownie-v2",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True
    })
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    
    # SSEフォーマットの検証
    content = response.text
    lines = content.strip().split("\n\n")
    assert len(lines) == 2
    
    data_chunk = lines[0].replace("data: ", "")
    data_done = lines[1]
    
    chunk_json = json.loads(data_chunk)
    assert chunk_json["choices"][0]["delta"]["content"] == "Hi there from stream!"
    assert chunk_json["choices"][0]["finish_reason"] == "stop"
    assert data_done == "data: [DONE]"