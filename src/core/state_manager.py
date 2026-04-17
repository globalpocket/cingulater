import operator
import os
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import redis.asyncio as redis
from langgraph.checkpoint.redis.aio import RedisSaver
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
    agent_specific_schemas: Dict[str, Any] # エージェントごとの Pull スキーマ
    validated_plan: Dict[str, Any]
    
    # Phase 3: Execution
    execution_tasks: List[str] # Huey に投入予定のタスクID
    execution_logs: List[Dict[str, Any]]
    execution_result_summary: str
    
    # Phase 4: Governance & Repair
    repair_needed: bool
    error_context: Optional[str]
    ringi_document: Optional[str] # 稟議書 (Human-in-the-loop 用)
    governance_decision: Optional[str] # 'Approve', 'Reject', 'NeedsRevision'
    
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
        # Redis 接続情報
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self._pool: Optional[redis.ConnectionPool] = None
        self._saver: Optional[RedisSaver] = None
        self._workflow_app = None

    async def __aenter__(self):
        """async with ステートメントでチェックポインタを有効化する"""
        if not self._saver:
            logger.debug(f"Connecting to Redis Checkpointer at {self.redis_host}:{self.redis_port}")
            self._pool = redis.ConnectionPool(
                host=self.redis_host, 
                port=self.redis_port, 
                db=0,
                decode_responses=False # Checkpointer internally handles bytes
            )
            # aio クライアントを使用
            client = redis.Redis(connection_pool=self._pool)
            self._saver = RedisSaver(client)
        
        # ワークフローのコンパイル (循環参照を避けるためにメソッド内でインポート)
        from src.core.graph.builder import compile_workflow
        self._workflow_app = compile_workflow(checkpointer=self._saver)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        async with ステートメント終了時にチェックポインタを閉じる
        """
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
            self._saver = None
            self._workflow_app = None

    @property
    def workflow_app(self):
        """
        コンパイル済みワークフローアプリケーションを取得する
        """
        if not self._workflow_app:
            # チェックポインタなしでコンパイル
            from src.core.graph.builder import compile_workflow
            self._workflow_app = compile_workflow(checkpointer=self._saver)
        return self._workflow_app

    async def get_state(self, thread_id: str) -> Dict[str, Any]:
        """指定した thread_id の最新状態を取得する"""
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.workflow_app.aget_state(config)
        return state.values if state else {}

    async def get_current_status(self, thread_id: str) -> str:
        """現在のステータス（Phase名など）を取得する"""
        values = await self.get_state(thread_id)
        return values.get("status", "Unknown")

    async def is_terminal_state(self, thread_id: str) -> bool:
        """タスクが完了または失敗状態にあるかを判定する"""
        status = await self.get_current_status(thread_id)
        return status in ["Completed", "Failed"]

    async def update_state(self, thread_id: str, values: Dict[str, Any], as_node: Optional[str] = None):
        """状態を更新する"""
        config = {"configurable": {"thread_id": thread_id}}
        logger.debug(f"Updating state for thread {thread_id} (node: {as_node})")
        return await self.workflow_app.aupdate_state(config, values, as_node=as_node)

    async def astream(self, thread_id: str, input_data: Dict[str, Any]):
        """ワークフローを非同期ストリームで実行する"""
        config = {"configurable": {"thread_id": thread_id}}
        async for event in self.workflow_app.astream(input_data, config=config):
            yield event
