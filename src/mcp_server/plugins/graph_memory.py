
import networkx as nx

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("graph_memory")

# 今回はデモ用としてインメモリグラフとし、必要に応じてSQLiteに永続化するベース
_graph = nx.DiGraph()

@mcp.tool()
@mcp_tool_errorhandler
async def add_entity_relation(source: str, relation: str, target: str) -> str:
    """エンティティ間の関係（依存関係など）をグラフ構造に記録します。"""
    _graph.add_edge(source, target, relation=relation)
    return f"Recorded: {source} -[{relation}]-> {target}"

@mcp.tool()
@mcp_tool_errorhandler
async def get_entity_relations(entity: str) -> str:
    """指定されたエンティティの関連情報を取得します。"""
    if entity not in _graph:
        return f"Entity '{entity}' not found."
    
    relations = []
    for neighbor in _graph.neighbors(entity):
        rel = _graph.edges[entity, neighbor].get('relation', 'unknown')
        relations.append(f"- {rel}-> {neighbor}")
        
    for pred in _graph.predecessors(entity):
        rel = _graph.edges[pred, entity].get('relation', 'unknown')
        relations.append(f"<-{rel}- {pred}")
        
    return "\n".join(relations)

if __name__ == "__main__":
    mcp.run(transport="stdio")
