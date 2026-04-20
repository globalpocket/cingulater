import operator
import os
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from loguru import logger


class TaskState(TypedDict):
    """
    Brownie 5-Phase Architecture 状態定義
    """

    # 基本情報
    task_id: str
    instruction: str
    repo_path: str
    status: str  # Phase 名や 'Completed', 'Failed', 'Waiting' 等

    # Phase 0: Intent Alignment
    intent_confirmed: bool
    evaluation_axes: List[str]
    intent_draft: str
    required_mcp_servers: List[str]

    # Phase 1: Core Analysis
    dependency_tree: Dict[str, Any]
    analysis_data: Dict[str, Any]
    high_info_gain_questions: List[str]

    # Phase 2: Handshake
    target_specialized_agents: List[str]
    agent_specific_schemas: Dict[str, Any]  # エージェントごとの Pull スキーマ
    validated_plan: Dict[str, Any]

    # Phase 3: Execution
    execution_tasks: List[str]  # タスクキューに投入予定のタスクID
    execution_logs: List[Dict[str, Any]]
    execution_result_summary: str

    # Phase 4: Governance & Repair
    repair_needed: bool
    error_context: Optional[str]
    ringi_document: Optional[str]  # 稟議書 (Human-in-the-loop 用)
    governance_decision: Optional[str]  # 'Approve', 'Reject', 'NeedsRevision'

    # Phase 5: 実行・完了情報
    topic_branch: Optional[str]
    has_changes: bool
    test_results: Optional[Dict[str, Any]]
    pr_url: Optional[str]

    # 履歴とログ
    reported_nodes: List[str]
    history: Annotated[List[Dict[str, Any]], operator.add]
    metadata: Dict[str, Any]


class StateManager:
    """
    LangGraph の状態管理（チェックポインタ）をラップするクラス
    """

    def __init__(self, db_path: Optional[str] = None):
        from src.core.config import get_settings

        self.settings = get_settings()
        self.db_path = db_path or self.settings.database.db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._saver: Optional[AsyncSqliteSaver] = None

    @property
    def saver(self) -> Optional[AsyncSqliteSaver]:
        """チェックポインタ（セーバー）を取得する"""
        return self._saver

    async def connect(self):
        """チェックポインタに接続する"""
        if not self._saver:
            logger.debug(f"Connecting to SQLite Checkpointer at {self.db_path}")
            # SQLite ファイルへの非同期接続
            db_full_path = os.path.expanduser(self.db_path)
            os.makedirs(os.path.dirname(db_full_path), exist_ok=True)
            self._conn = await aiosqlite.connect(db_full_path)
            self._saver = AsyncSqliteSaver(self._conn)
        return self

    async def __aenter__(self):
        """async with ステートメントでチェックポインタを有効化する"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        async with ステートメント終了時にチェックポインタを閉じる
        """
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._saver = None

    async def get_state_lightweight(self, thread_id: str) -> Dict[str, Any]:
        """
        グラフのコンパイルを行わずに、チェックポインタから直接最新状態を取得する。
        """
        if not self._saver:
            await self.connect()

        config = {"configurable": {"thread_id": thread_id}}
        state = await self._saver.aget(config)
        return state.values if state else {}
