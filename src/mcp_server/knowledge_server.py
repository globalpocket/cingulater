"""
BROWNIE Knowledge MCP Server
=============================
リポジトリの構造解析とセマンティック検索を公開するサーバー。
ロジックは src.core.knowledge_base.KnowledgeBaseProvider に委譲する。
"""

import asyncio
import json
import os
import sys
from typing import Optional

from src.core.knowledge_base import KnowledgeBaseProvider

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("knowledge_server")

# --- サーバーインスタンスの生成 ---
mcp = create_mcp_server("BrownieKnowledge")

# --- グローバル状態 ---
_repo_path: str = ""
_repo_name: str = ""
_memory_path: str = ""
_provider: Optional[KnowledgeBaseProvider] = None
_memory = None


def _get_provider():
    global _provider
    if _provider is None:
        _provider = KnowledgeBaseProvider(_repo_path, _repo_name)
    return _provider


def _get_memory():
    global _memory
    if _memory is None:
        from .history_server import HistoryServer
        _memory = HistoryServer()
    return _memory


@mcp.tool()
@mcp_tool_errorhandler
async def semantic_search(query: str, limit: int = 5) -> str:
    """コードベースからセマンティック検索を実行します。"""
    memory = _get_memory()
    # ChromaDB 検索
    results = await asyncio.to_thread(
        _sync_search, memory, query, _repo_name, limit
    )
    return json.dumps(results, ensure_ascii=False, indent=2)


def _sync_search(memory, query, repo_name, limit):
    results = memory.collection.query(
        query_texts=[query], where={"repo_name": repo_name}, n_results=limit
    )
    memories = []
    if results["documents"] and results["documents"][0]:
        for i in range(len(results["documents"][0])):
            memories.append({
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
    return memories


@mcp.tool()
@mcp_tool_errorhandler
async def get_code_flow(entry_symbol: str, depth: int = 5) -> str:
    """シンボル名から始まる処理フローを追跡します。"""
    provider = _get_provider()
    flow_data = await asyncio.to_thread(
        provider.tracer.trace_flow, entry_symbol, int(depth)
    )
    return f"### {entry_symbol} の処理フロー\n\n```mermaid\n{flow_data}\n```"


@mcp.tool()
@mcp_tool_errorhandler
async def get_repo_summary() -> str:
    """リポジトリの構造サマリーを返します。"""
    provider = _get_provider()
    summary = await asyncio.to_thread(provider.get_summary)
    return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.resource("brownie://repo/context")
async def repo_context() -> str:
    """プロジェクトの全体像を把握するための WDCA コンテキスト。"""
    provider = _get_provider()
    summary = await asyncio.to_thread(provider.get_summary)
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _init_from_args():
    global _repo_path, _memory_path, _repo_name
    if len(sys.argv) < 4:
        sys.exit(1)
    _repo_path = os.environ.get("BROWNIE_REPO_PATH", os.path.realpath(sys.argv[1]))
    _repo_name = os.environ.get("BROWNIE_TARGET_REPO", sys.argv[3])
    _memory_path = os.environ.get("BROWNIE_MEMORY_PATH", os.path.realpath(sys.argv[2]))


if __name__ == "__main__":
    _init_from_args()
    mcp.run(transport="stdio")
