import os
import sys
from typing import Any, Dict, Optional

from fastmcp import FastMCP
from loguru import logger

from src.core.config import get_settings
from src.core.orchestrator import Orchestrator
from src.core.state_manager import StateManager
from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# ロギング設定
logger = setup_logging("brownie_agent_server")
mcp = create_mcp_server("Brownie Agent Server")

# グローバルなオーケストレーターとステートマネージャーの初期化用
_orchestrator: Optional[Orchestrator] = None
_state_manager: Optional[StateManager] = None

def get_orchestrator() -> Orchestrator:
    """オーケストレーターのシングルトンインスタンスを取得または生成します。"""
    global _orchestrator
    if _orchestrator is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(project_root, "config", "config.yaml")
        _orchestrator = Orchestrator(config_path)
    return _orchestrator

def get_state_manager() -> StateManager:
    """ステートマネージャーのシングルトンインスタンスを取得または生成します。"""
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
    
    Args:
        repo_name: 対象のリポジトリ名 (例: "owner/repo")
        issue_number: 対象の Issue 番号
        task_description: タスクの補足説明（任意）
    """
    orch = get_orchestrator()
    task_id = f"{repo_name}#{issue_number}"
    
    logger.info(f"MCP Tool: Submitting task {task_id}")
    
    payload = {"description": task_description} if task_description else {}
    
    # Orchestrator の内部的なキュー投入ロジックを呼び出す
    # 注: 実際の実装では、ここで Worker Controller 等を通じてキューに入れる
    await orch.mcp_manager.worker_controller_client.call_tool(
        "enqueue_task",
        task_type="analysis",
        task_id=task_id,
        repo_name=repo_name,
        issue_number=issue_number,
        payload=payload
    )
    
    return f"Successfully submitted task: {task_id}. Brownie is now analyzing the issue."

@mcp.tool()
@mcp_tool_errorhandler
async def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    実行中のタスクの進捗状況を取得します。
    
    Args:
        task_id: タスク識別子 (例: "owner/repo#123")
    """
    sm = get_state_manager()
    state = await sm.get_state(task_id)
    
    if not state or not state.values:
        return {"task_id": task_id, "status": "NotFound"}
    
    return {
        "task_id": task_id,
        "status": state.values.get("status", "Unknown"),
        "current_node": getattr(state, "next", ["-"])[0],
        "last_summary": state.values.get("final_summary", state.values.get("plan", "No summary yet."))
    }

if __name__ == "__main__":
    # 標準入力/標準出力経由で MCP 通信を開始
    mcp.run(transport="stdio")
