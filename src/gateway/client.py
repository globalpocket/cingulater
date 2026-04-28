import os
import logging
from typing import Dict, Any, List, Optional
from contextlib import AsyncExitStack
import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)

class GatewayClient:
    """
    Brownie から MCP Routing Gateway に接続し、ツール一覧の取得や実行を行うクライアント。
    mcp-routing-gateway の BackendClient をベースに、単一接続に最適化。
    """
    def __init__(self, command: str = "mcp-gateway", args: Optional[List[str]] = None):
        # 起動するゲートウェイコマンド (必要に応じて config.yaml のパスなどを args に渡す)
        self.command = command
        self.args = args or []
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()

    async def start(self):
        """ゲートウェイプロセス(stdio)を起動し、セッションを確立する"""
        try:
            # 環境変数をそのまま引き継いで実行
            server_params = StdioServerParameters(
                command=self.command, 
                args=self.args, 
                env=os.environ.copy()
            )
            
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            read_stream, write_stream = stdio_transport
            
            self.session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            
            # 初期化ハンドシェイク
            await self.session.initialize()
            
            logger.info("✅ Successfully connected to MCP Routing Gateway.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Gateway: {e}")
            raise

    async def stop(self):
        """ゲートウェイプロセスを安全に終了させる"""
        await self._exit_stack.aclose()
        self.session = None
        logger.info("Gateway connection closed.")

    async def fetch_tools(self) -> List[Dict[str, Any]]:
        """ゲートウェイが Pydantic で生成した安全な仮想ツール一覧を取得する"""
        if not self.session:
            logger.error("Gateway is not connected.")
            return []
        
        try:
            tools_result = await self.session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema
                }
                for t in tools_result.tools
            ]
        except Exception as e:
            logger.error(f"Failed to fetch tools from Gateway: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """LLMからのツール実行要求をゲートウェイへ送信する"""
        if not self.session:
            raise ValueError("Gateway is not connected.")
        
        logger.info(f"Calling virtual tool '{tool_name}' via Gateway")
        result = await self.session.call_tool(tool_name, arguments)
        
        # MCP の結果 (TextContent等) を LLM が読みやすい単一の文字列にパース
        output = ""
        for content in result.content:
            if isinstance(content, types.TextContent):
                output += content.text + "\n"
            else:
                # 画像などテキスト以外のコンテンツの場合のフォールバック
                output += f"[{content.type} content]\n"
                
        return output.strip()