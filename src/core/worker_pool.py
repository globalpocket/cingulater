import logging
from src.core.workers.pool import huey

logger = logging.getLogger(__name__)

class WorkerPool:
    def __init__(self, project_root=None):
        self.huey = huey

    async def run(self):
        logger.info("WorkerPool: Active (FileHuey Mode)")

    def stop(self):
        pass

    async def add_task(self, task_id, priority, repo_name, issue_number, **kwargs):
        from src.core.workers.tasks import analysis_task
        logger.info(f"Queueing task {task_id} via FileHuey...")
        try:
            # Huey タスクとして投入
            analysis_task(task_id, repo_name, issue_number, kwargs)
            logger.info(f"Task {task_id} successfully persisted to filesystem.")
            return True
        except Exception as e:
            logger.error(f"Task enqueue FAILED: {e}")
            return False
