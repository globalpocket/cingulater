import pytest
from unittest.mock import patch, MagicMock
from core.config import Settings
from core.router import Router

@pytest.fixture
def router():
    settings = Settings()
    return Router(settings)

@pytest.mark.asyncio
async def test_route_empty_query(router):
    """空のクエリは即座に interlocutor になる"""
    assert await router.route("") == "interlocutor"

@pytest.mark.asyncio
async def test_route_heuristic_coder(router):
    """キーワードが含まれる場合は LLM を呼ばずに coder を返す"""
    with patch("httpx.AsyncClient.post") as mock_post:
        assert await router.route("Pythonのコードを修正して") == "coder"
        assert await router.route("バグがあるみたい") == "coder"
        # APIが一切呼ばれていないこと（ヒューリスティックによる高速判定）の証明
        mock_post.assert_not_called()

@pytest.mark.asyncio
async def test_route_llm_fallback_interlocutor(router):
    """キーワードがなく、LLM が interlocutor と判定した場合"""
    # レスポンスは同期オブジェクトなので MagicMock を使用する
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "interlocutor"}}]
    }
    
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        # キーワードを含まない日常会話
        assert await router.route("こんにちは、調子はどう？") == "interlocutor"

@pytest.mark.asyncio
async def test_route_llm_fallback_coder(router):
    """キーワードはないが、LLMの文脈理解で coder と判定した場合"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "coder"}}]
    }
    
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        # キーワードはないが、プログラミング系の要求
        assert await router.route("このアルゴリズムをもっと速くできないかな？") == "coder"

@pytest.mark.asyncio
@patch("core.router.logger.error")
async def test_route_llm_error_fallback(mock_logger_error, router):
    """LLM呼び出しでエラーが発生した場合は安全に interlocutor にフォールバックする"""
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection Timeout")):
        assert await router.route("複雑な要求") == "interlocutor"
        
        # エラーログが記録されていることを検証
        mock_logger_error.assert_called_once()
        assert "Router LLM Error" in mock_logger_error.call_args[0][0]