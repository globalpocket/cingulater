import httpx

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("web_fetch")

@mcp.tool()
@mcp_tool_errorhandler
async def fetch_web_content(url: str) -> str:
    """
    指定されたURLのWebページを取得し、その内容（HTML文字列）を返します。
    Markdown化は呼び出し側のエージェントで行うか、後日拡張可能です。
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return f"Error: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
