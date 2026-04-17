import os
import asyncio
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("db_profiler")

class DBProxy:
    def __init__(self):
        self.sqlite_client: Optional[Client] = None

    async def _get_sqlite_client(self) -> Client:
        if self.sqlite_client:
            return self.sqlite_client
        
        logger.info("Initializing official SQLite MCP sub-server...")
        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-sqlite"]
        )
        self.sqlite_client = Client(transport)
        await self.sqlite_client.initialize()
        return self.sqlite_client

db_proxy = DBProxy()

@mcp.tool()
@mcp_tool_errorhandler
async def profile_database_schema(db_path: str) -> str:
    """指定されたSQLiteデータベースファイルのスキーマや構成を分析し、最適化の提案を行います。"""
    if not os.path.exists(db_path):
        return f"Error: Database file not found {db_path}"
        
    client = await db_proxy._get_sqlite_client()
    
    try:
        # 1. テーブル一覧の取得
        res_tables = await client.call_tool("list_tables", dbPath=db_path)
        tables = res_tables.get("tables", [])
        
        report = ["Database Schema Profile (via Official SQLite MCP):"]
        for table in tables:
            report.append(f"\nTable: {table}")
            
            # 2. スキーマ詳細の取得
            res_schema = await client.call_tool("describe_table", dbPath=db_path, tableName=table)
            schema = res_schema.get("schema", "No schema available")
            report.append(f"  Schema: {schema}")
            
        return "\n".join(report)
    except Exception as e:
        logger.error(f"Profiling failed via official MCP: {e}")
        return f"Profiling failed: {e}. (Falling back to manual check not allowed under new pure architecture)"

if __name__ == "__main__":
    mcp.run(transport="stdio")
