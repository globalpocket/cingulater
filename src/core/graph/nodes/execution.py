from typing import Dict, Any
from src.core.state_manager import TaskState
from src.core.workers.tasks import execution_task


async def execution_delegation_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 3: Execution Delegation
    Huey に実行タスクを Pull させる。
    """
    print(f"--- Phase 3: Execution Delegation ({state['task_id']}) ---")

    # ワーカーの結果がまだ無い場合
    current_status = state.get("status")
    if current_status not in ["Execution_Completed", "Execution_Failed"]:
        print(f"Enqueuing execution_task for {state['task_id']}...")
        # 実際は Phase 2 で生成されたプランを渡す
        plan = state.get("validated_plan", "No plan provided.")
        repo_name = state["task_id"].split("#")[0]
        issue_number = int(state["task_id"].split("#")[1])
        execution_task(state["task_id"], repo_name, issue_number, {"plan": str(plan)})

        return {
            "status": "Waiting_Execution",
            "history": [{"node": "execution_delegation", "status": "enqueued"}],
        }

    # ワーカーが結果を書き戻した後の処理
    print(f"Execution finished with status: {current_status}")
    return {
        "status": current_status,
        "history": [{"node": "execution_delegation", "status": current_status.lower()}],
    }
