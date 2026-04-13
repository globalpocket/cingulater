import logging
import asyncio
import os
import sys
from dotenv import load_dotenv

# .env を確実にロード
load_dotenv()

# 自身のパスを最優先に設定
sys.path.insert(0, os.getcwd())

from src.core.workers.pool import huey
from src.core.orchestrator import Orchestrator

logger = logging.getLogger("brownie.worker")

# プロセスごとに1つのオーケストレーターを使い回す
_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        config_path = os.getenv("BROWNIE_CONFIG", "config/config.yaml")
        # トークンチェックも行う
        if not os.getenv("GITHUB_TOKEN"):
             logger.error("FATAL: GITHUB_TOKEN is not found in worker process even after load_dotenv()")
        _orchestrator = Orchestrator(config_path)
    return _orchestrator

@huey.task()
def analysis_task(task_id, repo_name, issue_number, payload):
    logger.info(f"!!! WORKER RECEIVED REAL TASK: {task_id} !!!")
    orch = get_orchestrator()
    try:
        # asyncio.run で非同期タスクを実行 (10分のタイムアウトを設定)
        asyncio.run(asyncio.wait_for(
            orch._execute_task(task_id, repo_name, issue_number, payload),
            timeout=600
        ))
        logger.info(f"!!! WORKER COMPLETED EXECUTION FOR {task_id} !!!")
    except asyncio.TimeoutError:
        logger.error(f"Worker task TIMEOUT after 600s: {task_id}")
    except Exception as e:
        logger.error(f"Worker execution failed: {e}", exc_info=True)

@huey.task()
def execution_task(task_id, repo_name, issue_number, payload):
    analysis_task(task_id, repo_name, issue_number, payload)

@huey.task()
def repair_task(task_id, repo_name, issue_number, payload):
    analysis_task(task_id, repo_name, issue_number, payload)
