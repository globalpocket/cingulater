import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from mcp.server.fastmcp import FastMCP

# ロギングの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("persistence_server")

# FastMCP サーバーの初期化
mcp = FastMCP("Persistence Server")

# データベースパスの設定 (デフォルト)
DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/brownie/persistence.db")

def get_db_path():
    return os.getenv("BROWNIE_PERSISTENCE_DB", DEFAULT_DB_PATH)

def init_db():
    """データベースとテーブルの初期化"""
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        
        # 1. 監視対象リポジトリ
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watched_repositories (
                repo_name TEXT PRIMARY KEY,
                last_polled_at DATETIME,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        
        # 2. 処理済みメンション
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_mentions (
                mention_id TEXT PRIMARY KEY,
                repo_name TEXT,
                issue_number INTEGER,
                updated_at TEXT,
                body TEXT,
                processed_at DATETIME,
                node_id TEXT,
                url TEXT,
                html_url TEXT,
                user_login TEXT,
                created_at TEXT,
                author_association TEXT,
                reactions TEXT
            )
        """)
        conn.commit()
        logger.info(f"Database initialized at {db_path}")
    finally:
        conn.close()

# 起動時に一度初期化
init_db()

@mcp.tool()
async def check_mention_status(mention_id: str, updated_at: str) -> str:
    """
    メンションが新規か、更新されているか、変更なし値かを確認します。
    戻り値: "NEW", "UPDATED", "UNCHANGED"
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT updated_at FROM processed_mentions WHERE mention_id = ?", (mention_id,))
        row = cursor.fetchone()
        if not row:
            return "NEW"
        
        stored_updated_at = row[0]
        
        def parse_iso(dt_str):
            if not dt_str: return datetime.min
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
    finally:
        conn.close()

@mcp.tool()
async def register_processed_mention(mention_data: Dict[str, Any]) -> bool:
    """
    処理済みメンション情報を保存または更新します。
    """
    db_path = get_db_path()
    mention_id = str(mention_data.get('comment_id', mention_data.get('id', '')))
    if not mention_id:
        logger.error("No mention_id provided in mention_data")
        return False

    repo_name = mention_data.get('repo_name')
    issue_number = mention_data.get('number')
    updated_at = mention_data.get('updated_at')
    body = mention_data.get('body', '')
    
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO processed_mentions (
                mention_id, repo_name, issue_number, updated_at, body, processed_at,
                node_id, url, html_url, user_login, created_at, author_association, reactions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mention_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                body = excluded.body,
                processed_at = excluded.processed_at,
                node_id = excluded.node_id,
                url = excluded.url,
                html_url = excluded.html_url,
                user_login = excluded.user_login,
                created_at = excluded.created_at,
                author_association = excluded.author_association,
                reactions = excluded.reactions
        """, (
            mention_id, repo_name, issue_number, updated_at, body, datetime.utcnow().isoformat(),
            mention_data.get('node_id'), mention_data.get('url'), mention_data.get('html_url'),
            mention_data.get('user_login'), mention_data.get('created_at'),
            mention_data.get('author_association'), str(mention_data.get('reactions', ''))
        ))
        conn.commit()
        logger.info(f"Registered mention {mention_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to register mention: {e}")
        return False
    finally:
        conn.close()

@mcp.tool()
async def list_watched_repositories() -> List[str]:
    """監視対象のリポジトリ一覧を取得します。"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT repo_name FROM watched_repositories WHERE is_active = 1")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

@mcp.tool()
async def upsert_repository(repo_name: str) -> bool:
    """リポジトリを監視リストに追加または更新します。"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO watched_repositories (repo_name, last_polled_at, is_active)
            VALUES (?, ?, 1)
            ON CONFLICT(repo_name) DO UPDATE SET
                last_polled_at = excluded.last_polled_at,
                is_active = 1
        """, (repo_name, datetime.utcnow().isoformat()))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to upsert repository: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    mcp.run()
