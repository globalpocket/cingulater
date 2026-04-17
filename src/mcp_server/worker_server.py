import os
import subprocess
from typing import Any, Dict, Optional

from loguru import logger

from src.core.workers.pool import broker

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# ロギングの設定
logger = setup_logging("worker_server")
mcp = create_mcp_server("Worker Server")

class WorkerService:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.consumer_proc: Optional[subprocess.Popen] = None
        self.active_tasks: Dict[str, str] = {} # task_id -> taskiq_task_id

    async def start_consumer(self):
        """Taskiq コンシューマーを起動"""
        if self.consumer_proc and self.consumer_proc.poll() is None:
            return "Already running"

        # Taskiq ワーカーの起動コマンド
        # .venv 内の python -m taskiq を使用するか、直接 taskiq コマンドを叩く
        venv_python = os.path.join(self.project_root, ".venv", "bin", "python")
        if not os.path.exists(venv_python):
            venv_python = "python"

        logger.info(f"Starting Taskiq worker from {self.project_root}...")
        
        os.makedirs(os.path.join(self.project_root, "logs"), exist_ok=True)
        stdout_log = open(os.path.join(self.project_root, "logs", "taskiq_stdout.log"), "a")
        stderr_log = open(os.path.join(self.project_root, "logs", "taskiq_stderr.log"), "a")

        # taskiq worker <path.to.module:broker>
        self.consumer_proc = subprocess.Popen(
            [venv_python, "-m", "taskiq", "worker", "src.core.workers.tasks:broker"],
            cwd=self.project_root,
            stdout=stdout_log,
            stderr=stderr_log,
            env={**os.environ, "PYTHONPATH": self.project_root}
        )
        return f"Started Taskiq worker (PID: {self.consumer_proc.pid})"

    def stop_consumer(self):
        if self.consumer_proc:
            logger.info("Stopping Taskiq worker...")
            self.consumer_proc.terminate()
            try:
                self.consumer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.consumer_proc.kill()
            self.consumer_proc = None
            return "Stopped"
        return "Not running"

    async def enqueue_task(self, task_id: str, repo_name: str, issue_number: int, payload: dict = None):
        from src.core.workers.tasks import analysis_task
        logger.info(f"Enqueuing Taskiq task {task_id}...")
        try:
            # Taskiq タスクとして投入
            kiq_task = await analysis_task.kiq(task_id, repo_name, issue_number, payload or {})
            t_id = kiq_task.task_id
            self.active_tasks[task_id] = t_id
            return t_id
        except Exception as e:
            logger.error(f"Failed to enqueue Taskiq task: {e}")
            raise

    def revoke_task(self, task_id: str):
        t_id = self.active_tasks.get(task_id)
        if t_id:
            # Taskiq でのキャンセルは、Broker や Backend の機能に依存するが、
            # 現時点では active_tasks からの削除のみ行い、後続の制御に任せる
            # (Taskiq レベルの強制キャンセルが必要な場合は追加の実装が必要)
            logger.warning(f"Taskiq cancellation for {t_id} is a placeholder (Active tasks entry removed).")
            del self.active_tasks[task_id]
            return f"Removed tracking for {t_id}"
        return f"Task {task_id} not found"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_service = WorkerService(PROJECT_ROOT)

@mcp.tool()
@mcp_tool_errorhandler
async def start_worker() -> str:
    """Worker プロセスを開始します。"""
    return await _service.start_consumer()

@mcp.tool()
@mcp_tool_errorhandler
async def stop_worker() -> str:
    """Worker プロセスを停止します。"""
    return _service.stop_consumer()

@mcp.tool()
@mcp_tool_errorhandler
async def enqueue_task(task_id: str, repo_name: str, issue_number: int, payload: Optional[Dict[str, Any]] = None) -> str:
    """タスクをキューに追加します。"""
    t_id = await _service.enqueue_task(task_id, repo_name, issue_number, payload=payload)
    return f"Successfully enqueued with Taskiq ID: {t_id}"

@mcp.tool()
@mcp_tool_errorhandler
async def cancel_task(task_id: str) -> str:
    """指定されたタスクをキャンセルします。"""
    return _service.revoke_task(task_id)

@mcp.tool()
@mcp_tool_errorhandler
async def get_worker_status() -> Dict[str, Any]:
    """Worker の健康状態とアクティブタスク一覧を取得します。"""
    is_running = _service.consumer_proc is not None and _service.consumer_proc.poll() is None
    return {
        "status": "RUNNING" if is_running else "STOPPED",
        "pid": _service.consumer_proc.pid if is_running else None,
        "active_tasks_count": len(_service.active_tasks),
        "active_tasks": list(_service.active_tasks.keys())
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
