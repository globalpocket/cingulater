import os
import sys
from typing import Any, Dict, Optional

from fastmcp import FastMCP
from loguru import logger

from src.core.config import get_settings
from src.core.state_manager import StateManager
from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# ロギング設定
logger = setup_logging("brownie_agent_server")
mcp = create_mcp_server("Brownie Agent Server")

# ステートマネージャー（軽量版）の保持用
_state_manager: Optional[StateManager] = None

def get_state_manager() -> StateManager:
    """ステートマネージャーのシングルトンインスタンスを取得します。"""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager

@mcp.tool()
@mcp_tool_errorhandler
async def submit_task(
    repo_name: str,
    issue_number: int,
    task_description: Optional[str] = None
) -> str:
    """
    Brownie に新しい開発タスクを依頼します。
    バックグラウンドで待機している Orchestrator プロセスのキューへタスクを直接投入します。
    """
    from src.core.workers.tasks import analysis_task
    
    task_id = f"{repo_name}#{issue_number}"
    logger.info(f"Driver: Submitting task {task_id} to background worker...")
    
    payload = {"description": task_description} if task_description else {}
    
    # Taskiq キューへの直接投入（Orchestrator をロードせずに実行可能）
    await analysis_task.kiq(
        task_id=task_id,
        repo_name=repo_name,
        issue_number=issue_number,
        payload=payload
    )
    
    return f"Successfully submitted task: {task_id}. The background Orchestrator is now starting analysis."

@mcp.tool()
@mcp_tool_errorhandler
async def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    実行中のタスクの進捗状況を取得します（グラフコンパイルを行わない軽量版）。
    """
    sm = get_state_manager()
    await sm.connect()  # Redis への接続を確実にする
    
    # グラフのコンパイル（多重起動）を避けるため、軽量メソッドを使用
    values = await sm.get_state_lightweight(task_id)
    
    if not values:
        return {"task_id": task_id, "status": "NotFound"}
    
    return {
        "task_id": task_id,
        "status": values.get("status", "Unknown"),
        "last_summary": values.get("final_summary", values.get("plan", "Processing...")),
        "updated_at": values.get("updated_at", "N/A")
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
