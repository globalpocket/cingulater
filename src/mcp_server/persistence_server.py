import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlmodel import Field, Session, SQLModel, create_engine, select

from src.core.config import get_settings

from .base_server import create_mcp_server, mcp_tool_errorhandler

mcp = create_mcp_server("Persistence Server")

# --- SQLModel 定義 ---

class WatchedRepository(SQLModel, table=True):
    __tablename__ = "watched_repositories"
    repo_name: str = Field(primary_key=True)
    last_polled_at: Optional[str] = None
    is_active: bool = Field(default=True)

class ProcessedMention(SQLModel, table=True):
    __tablename__ = "processed_mentions"
    mention_id: str = Field(primary_key=True)
    repo_name: Optional[str] = None
    issue_number: Optional[int] = None
    updated_at: Optional[str] = None
    body: Optional[str] = None
    processed_at: Optional[str] = None
    node_id: Optional[str] = None
    url: Optional[str] = None
    html_url: Optional[str] = None
    user_login: Optional[str] = None
    created_at: Optional[str] = None
    author_association: Optional[str] = None
    reactions: Optional[str] = None

# --- データベース初期化 ---

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = os.path.expanduser(settings.database.db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # WALモードなどを設定するために connect_args を使用可能
        _engine = create_engine(f"sqlite:///{db_path}")
    return _engine

def init_db():
    """データベースとテーブルの初期化"""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    logger.info("Database initialized with SQLModel")

# 起動時に一度初期化
init_db()

@mcp.tool()
@mcp_tool_errorhandler
async def check_mention_status(mention_id: str, updated_at: str) -> str:
    """
    メンションが新規か、更新されているか、変更なし値かを確認します。
    戻り値: "NEW", "UPDATED", "UNCHANGED"
    """
    engine = get_engine()
    with Session(engine) as session:
        statement = select(ProcessedMention).where(
            ProcessedMention.mention_id == mention_id
        )
        mention = session.exec(statement).first()
        
        if not mention:
            return "NEW"
        
        stored_updated_at = mention.updated_at
        
        def parse_iso(dt_str):
            if not dt_str:
                return datetime.min
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

    engine = get_engine()
    with Session(engine) as session:
        statement = select(ProcessedMention).where(
            ProcessedMention.mention_id == mention_id
        )
        mention = session.exec(statement).first()
        
        if not mention:
            mention = ProcessedMention(mention_id=mention_id)
            session.add(mention)
            
        mention.repo_name = mention_data.get('repo_name')
        mention.issue_number = mention_data.get('number')
        mention.updated_at = mention_data.get('updated_at')
        mention.body = mention_data.get('body', '')
        mention.processed_at = datetime.utcnow().isoformat()
        mention.node_id = mention_data.get('node_id')
        mention.url = mention_data.get('url')
        mention.html_url = mention_data.get('html_url')
        mention.user_login = mention_data.get('user_login')
        mention.created_at = mention_data.get('created_at')
        mention.author_association = mention_data.get('author_association')
        mention.reactions = str(mention_data.get('reactions', ''))
        
        session.commit()
        logger.info(f"Registered mention {mention_id} via SQLModel")
        return True

@mcp.tool()
@mcp_tool_errorhandler
async def list_watched_repositories() -> List[str]:
    """監視対象のリポジトリ一覧を取得します。"""
    engine = get_engine()
    with Session(engine) as session:
        statement = select(WatchedRepository).where(WatchedRepository.is_active)
        results = session.exec(statement).all()
        return [r.repo_name for r in results]

@mcp.tool()
@mcp_tool_errorhandler
async def upsert_repository(repo_name: str) -> bool:
    """リポジトリを監視リストに追加または更新します。"""
    engine = get_engine()
    with Session(engine) as session:
        statement = select(WatchedRepository).where(
            WatchedRepository.repo_name == repo_name
        )
        repo = session.exec(statement).first()
        
        if not repo:
            repo = WatchedRepository(repo_name=repo_name)
            session.add(repo)
            
        repo.last_polled_at = datetime.utcnow().isoformat()
        repo.is_active = True
        
        session.commit()
        return True

if __name__ == "__main__":
    mcp.run(transport="stdio")
