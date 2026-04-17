import httpx
import re
from loguru import logger

from ..base_server import create_mcp_server, mcp_tool_errorhandler

mcp = create_mcp_server("meta_search")

@mcp.tool()
@mcp_tool_errorhandler
async def search_web(query: str) -> str:
    """DuckDuckGo を使用して Web 検索を行い、上位の結果を返します。"""
    logger.info(f"Searching web for: {query} (Clean Mode)")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            url = f"https://duckduckgo.com/html/?q={query}"
            headers = {"User-Agent": "Mozilla/5.0"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            # BeautifulSoup を排除し、軽量な正規表現による抽出に純化（または公式 MCP へ委譲の布石）
            links = re.findall(r'href="(https?://[^"]+)"', response.text)
            
            # 不要なドメインを除外
            filtered = [l for l in links if "duckduckgo.com" not in l and "w3.org" not in l]
            
            if not filtered:
                return "No useful results found."
                
            return "Top URLs found:\n" + "\n".join(filtered[:5])
                
    except Exception as e:
        logger.error(f"Search proxy failed: {e}")
        return f"Search failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
