import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

class PersistenceManager:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """データベースとテーブルの初期化"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 監視対象リポジトリ管理テーブル
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watched_repositories (
                    repo_name TEXT PRIMARY KEY,
                    last_polled_at DATETIME,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            
            # 処理済みメンション管理テーブル (重複排除・編集検知)
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
            
            # マイグレーション: 不足しているカラムを追加
            try:
                cursor.execute("PRAGMA table_info(processed_mentions)")
                existing_columns = [row[1] for row in cursor.fetchall()]
                new_columns = [
                    ("node_id", "TEXT"),
                    ("url", "TEXT"),
                    ("html_url", "TEXT"),
                    ("user_login", "TEXT"),
                    ("created_at", "TEXT"),
                    ("author_association", "TEXT"),
                    ("reactions", "TEXT")
                ]
                for col_name, col_type in new_columns:
                    if col_name not in existing_columns:
                        logger.info(f"Adding column {col_name} to processed_mentions table.")
                        cursor.execute(f"ALTER TABLE processed_mentions ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                logger.warning(f"Migration error during _init_db: {e}")

            conn.commit()

    def upsert_repository(self, repo_name: str):
        """リポジトリを監視リストに追加または更新"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO watched_repositories (repo_name, last_polled_at, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(repo_name) DO UPDATE SET
                    last_polled_at = excluded.last_polled_at
            """, (repo_name, datetime.utcnow().isoformat()))
            conn.commit()

    def is_mention_new_or_updated(self, mention_id: str, updated_at: str) -> str:
        """メンションが新規または更新されているか確認 (NEW, UPDATED, UNCHANGED)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT updated_at FROM processed_mentions WHERE mention_id = ?", (mention_id,))
                row = cursor.fetchone()
                if not row:
                    return "NEW"
                
                stored_updated_at = row[0]
                
                # ISO 8601 文字列のパースと比較
                def parse_iso(dt_str):
                    if not dt_str: return datetime.min
                    # 'Z' サフィックスをタイムゾーン形式に変換
                    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

                try:
                    new_dt = parse_iso(updated_at)
                    stored_dt = parse_iso(stored_updated_at)
                    
                    if new_dt > stored_dt:
                        return "UPDATED"
                    else:
                        return "UNCHANGED"
                except (ValueError, TypeError):
                    # パース失敗時はフォールバックとして文字列比較
                    if updated_at > (stored_updated_at or ""):
                        return "UPDATED"
                    return "UNCHANGED"
        except Exception as e:
            logger.error(f"Error checking mention status: {e}")
            return "NEW"

    def save_processed_mention(self, mention_id: str, repo_name: str, issue_number: int, updated_at: str, body: str,
                               node_id: str = None, url: str = None, html_url: str = None,
                               user_login: str = None, created_at: str = None,
                               author_association: str = None, reactions: str = None):
        """メンションを処理済みとして保存または更新"""
        with sqlite3.connect(self.db_path) as conn:
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
            """, (mention_id, repo_name, issue_number, updated_at, body, datetime.utcnow().isoformat(),
                  node_id, url, html_url, user_login, created_at, author_association, reactions))
            conn.commit()

    def get_watched_repositories(self) -> List[str]:
        """アクティブな監視対象リポジトリ一覧を取得"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT repo_name FROM watched_repositories WHERE is_active = 1")
            return [row[0] for row in cursor.fetchall()]

    def deactivate_repository(self, repo_name: str):
        """リポジトリの監視を一時停止"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE watched_repositories SET is_active = 0 WHERE repo_name = ?", (repo_name,))
            conn.commit()
