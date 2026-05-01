# tests/test_core/test_llm_client.py
import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock
from core.llm_client import OpenAILLMClient, StandardLLMChunk, ToolCallChunk

@pytest.fixture
def client():
    return OpenAILLMClient()

@pytest.mark.asyncio
async def test_stream_chat_json_fallback(client):
    """Fallbackとして application/json が返ってきた場合のパーステスト"""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    
    fallback_json = json.dumps({
        "choices": [{
            "message": {
                "content": "Fallback response",
                "tool_calls": [{"id": "call_fallback", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}]
            },
            "finish_reason": "stop"
        }]
    }).encode("utf-8")
    
    mock_resp.aread.return_value = fallback_json
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        chunks = [c async for c in client.stream_chat("http://dummy", {}, 10)]
        
        assert len(chunks) == 1
        assert chunks[0].content == "Fallback response"
        assert len(chunks[0].tool_calls) == 1
        assert chunks[0].tool_calls[0].name == "test_tool"
        assert chunks[0].finish_reason == "stop"

@pytest.mark.asyncio
async def test_stream_chat_sse(client):
    """正常な Server-Sent Events (SSE) ストリーミングのパーステスト"""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    
    stream_data = [
        'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "my_tool", "arguments": ""}}]}}]}\n\n',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n',
        'data: [DONE]\n\n'
    ]
    
    async def aiter_lines():
        for line in stream_data:
            yield line
            
    mock_resp.aiter_lines = aiter_lines
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        chunks = [c async for c in client.stream_chat("http://dummy", {}, 10)]
        
        assert len(chunks) == 3
        assert chunks[0].content == "Hello"
        assert chunks[1].tool_calls[0].name == "my_tool"
        assert chunks[2].finish_reason == "stop"

@pytest.mark.asyncio
async def test_stream_chat_http_error(client):
    """HTTPエラー (500等) が返された際に例外が送出されるかのテスト"""
    mock_resp = AsyncMock()
    mock_resp.status_code = 500
    mock_resp.aread.return_value = b"Internal Server Error"
    
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
        with pytest.raises(Exception, match="LLM Error 500: Internal Server Error"):
            async for _ in client.stream_chat("http://dummy", {}, 10):
                pass