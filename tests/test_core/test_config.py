import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from gateway.client import GatewayClient
import mcp.types as types

@pytest.fixture
def client():
    return GatewayClient()

@pytest.mark.asyncio
async def test_start_stop(client):
    mock_stdio = AsyncMock()
    mock_stdio.__aenter__.return_value = (AsyncMock(), AsyncMock())
    mock_stdio.__aexit__ = AsyncMock()
    
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__ = AsyncMock()

    with patch("gateway.client.stdio_client", return_value=mock_stdio):
        with patch("gateway.client.ClientSession", return_value=mock_session_ctx):
            await client.start()
            assert client.session is not None
            mock_session.initialize.assert_called_once()
            
            await client.stop()
            assert client.session is None

@pytest.mark.asyncio
async def test_fetch_tools(client):
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
async def test_call_tool(client):
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
async def test_call_tool_not_connected(client):
    client.session = None
    with pytest.raises(ValueError):
        await client.call_tool("mock_tool", {})