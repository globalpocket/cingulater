from typing import Any, Dict

from loguru import logger

from src.core.state_manager import TaskState

logger = logging.getLogger("brownie.nodes.execution")


async def execution_delegation_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 3: Execution Delegation
    Taskiq に実行タスクを投入する。
    """
    print(f"--- Phase 3: Execution Delegation ({state['task_id']}) ---")

    # ワーカーの結果がまだ無い場合
    current_status = state.get("status")
    if current_status not in ["Execution_Completed", "Execution_Failed"]:
        logger.info(f"Enqueuing execution_task for {state['task_id']} via MCP...")
        
        # グローバルオーケストレーターから MCP マネージャーを取得
        from src.core.orchestrator import global_orchestrator
        mgr = global_orchestrator.mcp_manager if global_orchestrator else None
        client = mgr.worker_controller_client if mgr else None
        
        if not client:
            logger.error("Worker Controller MCP Client is not available.")
            return {"status": "Execution_Failed", "error": "Worker Controller not ready"}

        plan = state.get("validated_plan", "No plan provided.")
        repo_name = state["task_id"].split("#")[0]
        issue_number = int(state["task_id"].split("#")[1])

        # MCP ツールの呼び出し
        await client.call_tool(
            "enqueue_task",
            task_type="execution",
            task_id=state["task_id"],
            repo_name=repo_name,
            issue_number=issue_number,
            payload={"plan": str(plan)}
        )

        return {
            "status": "Waiting_Execution",
            "history": [{"node": "execution_delegation", "status": "enqueued_via_mcp"}],
        }

    # ワーカーが結果を書き戻した後の処理
    print(f"Execution finished with status: {current_status}")
    return {
        "status": current_status,
        "history": [{"node": "execution_delegation", "status": current_status.lower()}],
    }
