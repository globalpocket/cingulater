import asyncio
import json
from loguru import logger
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.core.trigger_manager import WorkflowTriggerManager
from src.core.workers.pool import huey
from src.core.workflow_manager import WorkflowLoader

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
        # get_settings() は内部で BROWNIE_CONFIG を参照する
        settings = get_settings()
        # トークンチェックも行う
        if not os.getenv("GITHUB_TOKEN"):
             logger.error("FATAL: GITHUB_TOKEN not found in worker process.")
        from src.core.orchestrator import Orchestrator
        _orchestrator = Orchestrator(os.getenv("BROWNIE_CONFIG", "config/config.yaml"))
    return _orchestrator

@huey.task(retries=0)
def analysis_task(task_id, repo_name, issue_number, payload):
    msg = f"!!! TASK RECEIVED: {task_id} (#{issue_number}) !!!"
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

# --- Dynamic Workflow Trigger Engine ---

@huey.task()
def execute_workflow_task(workflow_name: str, input_data: Any = None):
    """
    指定された動的ワークフローを実行する Huey タスク。
    """
    logger.info(f"🚀 Executing dynamic workflow: {workflow_name}")
    try:
        orch = get_orchestrator()
        # ワークフローローダーの初期化
        loader = WorkflowLoader(Path(orch.project_root))
        # ワークスペースがあればそれも考慮する (現状は Core 優先)
        tools = loader.load_all()
        
        if workflow_name not in tools:
            logger.error(f"Workflow '{workflow_name}' not found by loader.")
            return

        workflow_func = tools[workflow_name]
        
        async def _run():
            # LLM サーバーの準備を待つ
            await orch._wait_for_llm_ready()
            
            # MCP サーバー群のコンテキスト（LLM 推論に必要）
            async with orch.mcp_manager:
                # 必要なサーバーはワークフローの性質に依存するが、
                # インタープリター等はデフォルトで起動しておく
                await orch.mcp_manager.start_intent_interpreter_server()
                
                logger.info(f"Running workflow function for {workflow_name}...")
                result = await workflow_func(input_data=input_data)
                res_str = str(result)[:200]
                logger.info(f"✅ Workflow '{workflow_name}' finished. Result: {res_str}...")

        asyncio.run(_run())
    except Exception as e:
        logger.error(f"Error in execute_workflow_task for {workflow_name}: {e}")

@huey.periodic_task(huey.crontab(minute='*'))
def master_trigger_dispatcher():
    """
    1分ごとに起動し、登録されたワークフローのトリガーをチェックして、周期が来たものを発火させる。
    """
    now = datetime.now()
    logger.debug(f"⏰ Master trigger dispatcher running at {now.isoformat()}")
    
    try:
        orch = get_orchestrator()
        # ワークフローメタデータの読み込み
        loader = WorkflowLoader(Path(orch.project_root))
        # メタデータのみが必要なので、ダミーの config で高速ロード
        # (ただしトリガー定義をパースするために WorkflowLoader を使用)
        loader.load_all() 
        
        trigger_manager = WorkflowTriggerManager()
        due_workflows = trigger_manager.get_due_workflows(loader.registry._tools, now)
        
        if not due_workflows:
            return

        for job in due_workflows:
            name = job["workflow_name"]
            schedule = job["schedule"]
            logger.info(f"🔔 Trigger matched: '{name}' (Schedule: {schedule}).")
            
            # 入力データの構成 (User Request 仕様)
            input_data = {
                "trigger_type": "cron",
                "schedule": schedule,
                "executed_at": now.isoformat()
            }
            
            # Huey タスクを投入
            execute_workflow_task(name, input_data=input_data)
            
    except Exception as e:
        logger.error(f"Error in master_trigger_dispatcher: {e}")
