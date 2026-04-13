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
                    processed_at DATETIME
                )
            """)
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

    def is_mention_new_or_updated(self, mention_id: str, updated_at: str) -> bool:
        """メンションが新規または更新されているか確認"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT updated_at FROM processed_mentions WHERE mention_id = ?", (mention_id,))
            row = cursor.fetchone()
            if not row:
                return True # 新規
            
            # 保存されている updated_at と比較 (文字列比較)
            return updated_at > row[0]

    def save_processed_mention(self, mention_id: str, repo_name: str, issue_number: int, updated_at: str, body: str):
        """メンションを処理済みとして保存または更新"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO processed_mentions (mention_id, repo_name, issue_number, updated_at, body, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mention_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    body = excluded.body,
                    processed_at = excluded.processed_at
            """, (mention_id, repo_name, issue_number, updated_at, body, datetime.utcnow().isoformat()))
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
