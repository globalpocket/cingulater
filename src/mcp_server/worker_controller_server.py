from src.core.workers.tasks import analysis_task, execution_task, repair_task

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# ロギング設定
logger = setup_logging("worker_controller_server")
mcp = create_mcp_server("WorkerController")

@mcp.tool()
@mcp_tool_errorhandler
async def enqueue_task(
    task_type: str,
    task_id: str,
    repo_name: str,
    issue_number: int,
    payload: dict
) -> str:
    """
    バックグラウンドワーカーにタスクを投入します。
    
    Args:
        task_type: "execution", "analysis", "repair" のいずれか
        task_id: ユニークなタスク識別子
        repo_name: リポジトリ名 (owner/repo)
        issue_number: Issue 番号
        payload: タスクに渡す追加データ (プラン等)
    """
    logger.info(f"Enqueuing {task_type} task for {task_id}")
    
    if task_type == "execution":
        execution_task(task_id, repo_name, issue_number, payload)
    elif task_type == "analysis":
        analysis_task(task_id, repo_name, issue_number, payload)
    elif task_type == "repair":
        repair_task(task_id, repo_name, issue_number, payload)
    else:
        raise ValueError(f"Unknown task_type: {task_type}")

    return f"Successfully enqueued {task_type} task for {task_id}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
