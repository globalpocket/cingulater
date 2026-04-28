import pytest
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