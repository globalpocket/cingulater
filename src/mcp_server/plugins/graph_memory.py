import os
import asyncio
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("graph_memory")

class MemoryProxy:
    def __init__(self):
        self.client: Optional[Client] = None

    async def _get_client(self) -> Client:
        if self.client:
            return self.client
        
        logger.info("Initializing official Memory MCP sub-server...")
        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-memory"]
        )
        self.client = Client(transport)
        await self.client.initialize()
        return self.client

proxy = MemoryProxy()

@mcp.tool()
@mcp_tool_errorhandler
async def add_entity_relation(source: str, relation: str, target: str) -> str:
    """エンティティ間の関係（依存関係など）をグラフ構造に記録します。"""
    client = await proxy._get_client()
    
    # 1. エンティティの作成（存在しない場合のみ）
    await client.call_tool("create_entities", entities=[
        {"name": source, "entityType": "unknown", "observations": []},
        {"name": target, "entityType": "unknown", "observations": []}
    ])
    
    # 2. 関係の作成
    await client.call_tool("create_relations", relations=[
        {"from": source, "to": target, "relationType": relation}
    ])
    
    return f"Recorded via Official Memory MCP: {source} -[{relation}]-> {target}"

@mcp.tool()
@mcp_tool_errorhandler
async def get_entity_relations(entity: str) -> str:
    """指定されたエンティティの関連情報を取得します。"""
    client = await proxy._get_client()
    
    # 公式サーバーからグラフ全体を読み取り、対象エンティティに関連する部分を抽出
    res = await client.call_tool("read_graph")
    
    relations = []
    # read_graph の戻り値構造 (entities, relations) を想定
    for rel in res.get("relations", []):
        if rel["from"] == entity:
            relations.append(f"- {rel['relationType']}-> {rel['to']}")
        elif rel["to"] == entity:
            relations.append(f"<-{rel['relationType']}- {rel['from']}")
            
    if not relations:
        return f"Entity '{entity}' related info not found in official memory."
        
    return "\n".join(relations)

if __name__ == "__main__":
    mcp.run(transport="stdio")
