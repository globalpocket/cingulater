import os
import time
from typing import Any, Dict, List, Optional

import chromadb

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("history_server")
mcp = create_mcp_server("History Server")


class HistoryServer:
    """Vector DB (ChromaDB) へのアクセスを管理するクラス"""

    def __init__(self, persist_directory: Optional[str] = None):
        host = os.environ["CHROMADB_HOST"]
        port = int(os.environ["CHROMADB_PORT"])
        logger.info(f"Connecting to ChromaDB at {host}:{port}")

        try:
            self.client = chromadb.HttpClient(host=host, port=port)
            self.collection = self.client.get_or_create_collection(
                name="brownie_memories", metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            logger.error(f"Failed to connect to ChromaDB: {e}")
            raise

    def save_experience(
        self,
        repo_name: str,
        issue_id: int,
        scope: str,
        task_type: str,
        content: str,
        commit_hash: str,
    ):
        timestamp = time.time()
        doc_id = f"{repo_name}_{issue_id}_{scope}_{timestamp}"

        metadata = {
            "repo_name": repo_name,
            "issue_id": issue_id,
            "scope": scope,
            "type": task_type,
            "commit_hash": commit_hash,
            "last_modified": timestamp,
            "timestamp": timestamp,
        }

        self.collection.add(documents=[content], metadatas=[metadata], ids=[doc_id])
        return doc_id

    def search_memory(self, query: str, repo_name: str, limit: int = 5):
        results = self.collection.query(
            query_texts=[query], where={"repo_name": repo_name}, n_results=limit
        )

        memories = []
        if results["documents"] and results["documents"][0]:
            for i in range(len(results["documents"][0])):
                memories.append(
                    {
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "distance": results["distances"][0][i],
                    }
                )
        return memories

    def invalidate_index(self, repo_name: str, file_path_pattern: str):
        self.collection.delete(
            where={
                "$and": [
                    {"repo_name": {"$eq": repo_name}},
                    {"scope": {"$contains": file_path_pattern}},
                ]
            }
        )


# シングルトンインスタンス（遅延初期化）
_service = None


def get_service():
    global _service
    if _service is None:
        _service = HistoryServer()
    return _service


@mcp.tool()
@mcp_tool_errorhandler
async def save_experience(
    repo_name: str,
    issue_id: int,
    scope: str,
    task_type: str,
    content: str,
    commit_hash: str,
) -> str:
    """成功体験をベクトルDBに保存します。"""
    doc_id = get_service().save_experience(
        repo_name, issue_id, scope, task_type, content, commit_hash
    )
    return f"Saved experience with ID: {doc_id}"


@mcp.tool()
@mcp_tool_errorhandler
async def search_memories(
    query: str, repo_name: str, limit: int = 5
) -> List[Dict[str, Any]]:
    """関連する過去の経験を検索します。"""
    return get_service().search_memory(query, repo_name, limit)


@mcp.tool()
@mcp_tool_errorhandler
async def invalidate_memories(repo_name: str, file_path_pattern: str) -> str:
    """指定されたパターンの古い記憶を無効化（削除）します。"""
    get_service().invalidate_index(repo_name, file_path_pattern)
    return f"Invalidated memories for {file_path_pattern}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
