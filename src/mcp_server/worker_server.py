import logging
import sys
import os
import subprocess
import asyncio
from typing import Optional, Dict, Any, List
from fastmcp import FastMCP
from src.core.workers.pool import huey

# ロギングの設定
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("worker_server")

# FastMCP サーバーの初期化
mcp = FastMCP("Worker Server")

class WorkerService:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.consumer_proc: Optional[subprocess.Popen] = None
        self.active_tasks: Dict[str, str] = {} # task_id -> huey_task_id

    async def start_consumer(self):
        """Huey コンシューマーを起動"""
        if self.consumer_proc and self.consumer_proc.poll() is None:
            return "Already running"

        venv_huey = os.path.join(self.project_root, ".venv", "bin", "huey_consumer")
        if not os.path.exists(venv_huey):
            venv_huey = "huey_consumer"

        logger.info(f"Starting Huey consumer from {self.project_root}...")
        
        # ログディレクトリの確保
        os.makedirs(os.path.join(self.project_root, "logs"), exist_ok=True)
        stdout_log = open(os.path.join(self.project_root, "logs", "huey_stdout.log"), "a")
        stderr_log = open(os.path.join(self.project_root, "logs", "huey_stderr.log"), "a")

        self.consumer_proc = subprocess.Popen(
            [venv_huey, "src.core.workers.tasks.huey", "-w", "1"],
            cwd=self.project_root,
            stdout=stdout_log,
            stderr=stderr_log,
            env={**os.environ, "PYTHONPATH": self.project_root}
        )
        return f"Started Huey consumer (PID: {self.consumer_proc.pid})"

    def stop_consumer(self):
        if self.consumer_proc:
            logger.info("Stopping Huey consumer...")
            self.consumer_proc.terminate()
            try:
                self.consumer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.consumer_proc.kill()
            self.consumer_proc = None
            return "Stopped"
        return "Not running"

    def enqueue_task(self, task_id: str, repo_name: str, issue_number: int, priority: int = 1, payload: dict = None):
        from src.core.workers.tasks import analysis_task
        logger.info(f"Enqueuing task {task_id}...")
        try:
            # Huey タスクとして投入
            h_result = analysis_task(task_id, repo_name, issue_number, payload or {})
            h_id = h_result.id
            self.active_tasks[task_id] = h_id
            return h_id
        except Exception as e:
            logger.error(f"Failed to enqueue task: {e}")
            raise

    def revoke_task(self, task_id: str):
        h_id = self.active_tasks.get(task_id)
        if h_id:
            huey.revoke_by_id(h_id)
            del self.active_tasks[task_id]
            return f"Revoked {h_id}"
        return f"Task {task_id} not found"

# サーバー起動時にプロジェクトルートを特定
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_service = WorkerService(PROJECT_ROOT)

@mcp.tool()
async def start_worker() -> str:
    """Worker プロセスを開始します。"""
    return await _service.start_consumer()

@mcp.tool()
async def stop_worker() -> str:
    """Worker プロセスを停止します。"""
    return _service.stop_consumer()

@mcp.tool()
async def enqueue_task(task_id: str, repo_name: str, issue_number: int, payload: Optional[Dict[str, Any]] = None) -> str:
    """タスクをキューに追加します。"""
    try:
        h_id = _service.enqueue_task(task_id, repo_name, issue_number, payload=payload)
        return f"Successfully enqueued with ID: {h_id}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def cancel_task(task_id: str) -> str:
    """指定されたタスクをキャンセルします。"""
    return _service.revoke_task(task_id)

@mcp.tool()
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
