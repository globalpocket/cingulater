import os
from typing import Optional
from contextlib import AsyncExitStack

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from loguru import logger

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("git_archeology")
mcp = create_mcp_server("git_archeology")

# --- グローバル状態 ---
_git_client: Optional[Client] = None
_exit_stack = AsyncExitStack()

async def _get_git_client() -> Client:
    """Git MCP クライアントを遅延起動・取得する"""
    global _git_client
    if _git_client:
        return _git_client
    
    logger.info("Starting official Git MCP server for Archeology...")
    transport = StdioTransport(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git"]
    )
    
    client = Client(transport)
    await _exit_stack.enter_async_context(client)
    _git_client = client
    return client

@mcp.on_shutdown()
async def on_shutdown():
    logger.info("Stopping Git MCP client for Archeology...")
    await _exit_stack.aclose()

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_git_history(
    file_path: str, 
    line_start: int = None, 
    line_end: int = None, 
    repo_path: str = "."
) -> str:
    """指定されたファイルの過去のコミット履歴を解析します。"""
    client = await _get_git_client()
    
    # パスの存在確認（公式 MCP 側でも行われるが、事前ガード）
    if not os.path.exists(file_path):
        return f"Error: File not found {file_path}"
        
    try:
        if line_start and line_end:
            # 公式 Git MCP の git_blame を使用
            # 備考: 公式ツールが L 引数（行指定）をサポートしていない場合は、
            # 全体を取得して Python 側でフィルタリングする
            res = await client.call_tool("git_blame", {"directory": repo_path, "path": file_path})
            return f"Git History Analysis (Blame):\n{res}"
        else:
            # 公式 Git MCP の git_log を使用
            res = await client.call_tool("git_log", {"directory": repo_path})
            return f"Git History Analysis (Log):\n{res}"
    except Exception as e:
        return f"Archeology failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
