from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import httpx
import urllib.parse
from bs4 import BeautifulSoup

logger = setup_logging(__name__)
mcp = create_mcp_server("meta_search")

@mcp.tool()
@mcp_tool_errorhandler
async def search_web(query: str) -> str:
    """DuckDuckGo(HTML版)を使用した簡易Web検索を行います。"""
    try:
        async with httpx.AsyncClient() as client:
            url = "https://html.duckduckgo.com/html/"
            data = {"q": query}
            headers = {"User-Agent": "Mozilla/5.0"}
            response = await client.post(url, data=data, headers=headers, timeout=10.0)
            response.raise_for_status()
            
            # BeautifulSoupがインストールされていればパース、なければそのまま返す（要件に合わせて調整）
            try:
                soup = BeautifulSoup(response.text, 'html.parser')
                results = []
                for a in soup.find_all('a', class_='result__url'):
                    results.append(a.get('href'))
                if not results:
                    return "No results found or parsing failed."
                return "URLs found:\n" + "\n".join(results[:5])
            except ImportError:
                # beautifulsoup4がない場合のフォールバック（最初の2000文字）
                return response.text[:2000]
                
    except Exception as e:
        return f"Search failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
