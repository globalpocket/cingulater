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
import json
import time
import threading

logger = logging.getLogger("brownie.worker")

async def _async_heartbeat(orch, task_id):
    """5秒ごとにリソース状況を MCP 経由で取得し Redis に書き込む非同期ループ"""
    client = orch.mcp_manager.resource_monitor_client
    if not client:
        logger.error("Resource Monitor Client not ready for heartbeat.")
        return

    while True:
        try:
            # MCP サーバーはワーカープロセスのサブプロセスとして動いているため、
            # 指定なし (os.getppid()) でワーカー本体の PID が取得される
            metrics = await client.call_tool("get_process_resources")
            metrics["task_id"] = task_id
            metrics["timestamp"] = time.time()
            
            # Redis に書き込み (huey のコネクションを流用)
            conn = huey.storage.conn
            conn.set(f"brownie:heartbeat:{task_id}", json.dumps(metrics), ex=30)
            logger.debug(f"Heartbeat sent for {task_id}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        await asyncio.sleep(5)

# ロギングの初期化（ワーカープロセス用）
def setup_logging():
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    # logs ディレクトリが存在することを確認
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler("logs/brownie.log"),
            logging.StreamHandler(sys.stderr)
        ]
    )

setup_logging()

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

@huey.task(retries=0)
def analysis_task(task_id, repo_name, issue_number, payload):
    msg = f"!!! WORKER RECEIVED REAL TASK: {task_id} (Repo: {repo_name}, Issue: {issue_number}) !!!"
    logger.info(msg)
    print(msg, file=sys.stderr) # 強制出力
    
    try:
        orch = get_orchestrator()
        logger.info(f"Orchestrator initialized in worker for {task_id}.")
        
        async def run_worker_with_mcp():
            # MCP サーバーのライフサイクル管理
            async with orch.mcp_manager:
                await orch.mcp_manager.start_resource_monitor_server()
                
                # Heartbeat 開始
                hb_task = asyncio.create_task(_async_heartbeat(orch, task_id))
                
                try:
                    # 実際のタスク実行
                    await asyncio.wait_for(
                        orch._execute_task(task_id, repo_name, issue_number, payload),
                        timeout=600
                    )
                finally:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass
        
        # asyncio.run で非同期マシンの全容を実行
        asyncio.run(run_worker_with_mcp())
        logger.info(f"!!! WORKER COMPLETED EXECUTION FOR {task_id} !!!")
    except asyncio.TimeoutError:
        logger.error(f"Worker task TIMEOUT after 600s: {task_id}")
    except Exception as e:
        logger.exception(f"Worker execution failed with error: {e}")
        print(f"FATAL ERROR in worker: {e}", file=sys.stderr)
    finally:
        # Redis のエントリを削除
        try:
            huey.storage.conn.delete(f"brownie:heartbeat:{task_id}")
        except:
            pass

@huey.task(retries=0)
def execution_task(task_id, repo_name, issue_number, payload):
    analysis_task(task_id, repo_name, issue_number, payload)

@huey.task(retries=0)
def repair_task(task_id, repo_name, issue_number, payload):
    analysis_task(task_id, repo_name, issue_number, payload)
