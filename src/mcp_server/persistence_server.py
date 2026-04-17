import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from loguru import logger

from .base_server import create_mcp_server, mcp_tool_errorhandler

mcp = create_mcp_server("Persistence Server")

# --- Redis 設定 ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

_redis_client: Optional[redis.Redis] = None

def get_redis_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True
        )
    return _redis_client

# キーの接頭辞
KEY_PREFIX = "brownie:persistence"
KEY_WATCHED_REPOS = f"{KEY_PREFIX}:watched_repos"
KEY_PROCESSED_MENTION = f"{KEY_PREFIX}:processed_mention:"

@mcp.tool()
@mcp_tool_errorhandler
async def check_mention_status(mention_id: str, updated_at: str) -> str:
    """
    メンションが新規か、更新されているか、変更なし値かを確認します。
    戻り値: "NEW", "UPDATED", "UNCHANGED"
    """
    client = get_redis_client()
    key = f"{KEY_PROCESSED_MENTION}{mention_id}"
    
    stored_mention = await client.hgetall(key)
    
    if not stored_mention:
        return "NEW"
    
    stored_updated_at = stored_mention.get("updated_at")
    
    def parse_iso(dt_str):
        if not dt_str:
            return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

    try:
        new_dt = parse_iso(updated_at)
        stored_dt = parse_iso(stored_updated_at)
        if new_dt > stored_dt:
            return "UPDATED"
        else:
            return "UNCHANGED"
    except Exception:
        # フォールバックとして文字列比較
        if updated_at > (stored_updated_at or ""):
            return "UPDATED"
        return "UNCHANGED"

@mcp.tool()
@mcp_tool_errorhandler
async def register_processed_mention(mention_data: Dict[str, Any]) -> bool:
    """
    処理済みメンション情報を保存または更新します。
    """
    mention_id = str(mention_data.get('comment_id', mention_data.get('id', '')))
    if not mention_id:
        logger.error("No mention_id provided in mention_data")
        return False

    client = get_redis_client()
    key = f"{KEY_PROCESSED_MENTION}{mention_id}"
    
    # 保存するデータの構築
    data = {
        "mention_id": mention_id,
        "repo_name": mention_data.get('repo_name', ""),
        "issue_number": str(mention_data.get('number', "")),
        "updated_at": mention_data.get('updated_at', ""),
        "body": mention_data.get('body', ''),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "node_id": mention_data.get('node_id', ""),
        "url": mention_data.get('url', ""),
        "html_url": mention_data.get('html_url', ""),
        "user_login": mention_data.get('user_login', ""),
        "created_at": mention_data.get('created_at', ""),
        "author_association": mention_data.get('author_association', ""),
        "reactions": json.dumps(mention_data.get('reactions', {}))
    }
    
    # 既存の全フィールドを更新
    await client.hset(key, mapping=data)
    logger.info(f"Registered mention {mention_id} via Redis Hash")
    return True

@mcp.tool()
@mcp_tool_errorhandler
async def list_watched_repositories() -> List[str]:
    """監視対象のリポジトリ一覧を取得します。"""
    client = get_redis_client()
    repos = await client.smembers(KEY_WATCHED_REPOS)
    return list(repos)

@mcp.tool()
@mcp_tool_errorhandler
async def upsert_repository(repo_name: str) -> bool:
    """リポジトリを監視リストに追加または更新します。"""
    client = get_redis_client()
    await client.sadd(KEY_WATCHED_REPOS, repo_name)
    logger.info(f"Upserted repository {repo_name} to Redis Set")
    return True

if __name__ == "__main__":
    mcp.run(transport="stdio")
