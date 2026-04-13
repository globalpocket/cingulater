import logging
import subprocess
import sys
import os
from typing import Optional
from src.core.workers.pool import huey

logger = logging.getLogger(__name__)

class WorkerPool:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.huey = huey
        self.consumer_proc: Optional[subprocess.Popen] = None

    async def run(self):
        logger.info(f"WorkerPool: Active ({type(self.huey).__name__})")
        
        # Huey コンシューマーを別プロセスで起動 (設計書 2.2)
        venv_huey = os.path.join(self.project_root, ".venv", "bin", "huey_consumer")
        if not os.path.exists(venv_huey):
            venv_huey = "huey_consumer"

        logger.info("Starting Huey consumer...")
        try:
            # 標準出力を logs/huey_stdout.log にリダイレクト
            stdout_log = open(os.path.join(self.project_root, "logs", "huey_stdout.log"), "a")
            stderr_log = open(os.path.join(self.project_root, "logs", "huey_stderr.log"), "a")
            
            self.consumer_proc = subprocess.Popen(
                [venv_huey, "src.core.workers.tasks.huey", "-w", "1"],
                cwd=self.project_root,
                stdout=stdout_log,
                stderr=stderr_log,
                env={**os.environ, "PYTHONPATH": self.project_root}
            )
            logger.info(f"Huey consumer started (PID: {self.consumer_proc.pid})")
        except Exception as e:
            logger.error(f"Failed to start Huey consumer: {e}")

    def stop(self):
        if self.consumer_proc:
            logger.info(f"Stopping Huey consumer (PID: {self.consumer_proc.pid})...")
            self.consumer_proc.terminate()
            try:
                self.consumer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.consumer_proc.kill()
            self.consumer_proc = None

    async def add_task(self, task_id, priority, repo_name, issue_number, **kwargs):
        from src.core.workers.tasks import analysis_task
        logger.info(f"Queueing task {task_id} via {type(self.huey).__name__}...")
        try:
            # Huey タスクとして投入
            analysis_task(task_id, repo_name, issue_number, kwargs)
            logger.info(f"Task {task_id} successfully queued.")
            return True
        except Exception as e:
            logger.error(f"Task enqueue FAILED: {e}")
            return False
