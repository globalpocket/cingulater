import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from loguru import logger

from src.core.config import get_settings
from src.core.trigger_manager import WorkflowTriggerManager
from src.core.workers.pool import broker
from src.core.workflow_manager import WorkflowLoader

# プロセスごとに1つのオーケストレーターを使い回す
_orchestrator = None

async def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        # get_settings() は内部で BROWNIE_CONFIG を参照する
        settings = get_settings()
        if not os.getenv("GITHUB_TOKEN"):
             logger.error("FATAL: GITHUB_TOKEN not found in worker process.")
        from src.core.orchestrator import Orchestrator
        _orchestrator = Orchestrator(os.getenv("BROWNIE_CONFIG", "config/config.yaml"))
    return _orchestrator

async def _async_heartbeat(orch, task_id):
    """リソース状況を MCP 経由で取得し Redis に書き込む非同期ループ"""
    client = orch.mcp_manager.resource_monitor_client
    if not client:
        logger.error("Resource Monitor Client not ready for heartbeat.")
        return

    import redis.asyncio as aioredis
    from src.core.workers.pool import REDIS_HOST, REDIS_PORT
    
    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT)

    while True:
        try:
            metrics = await client.call_tool("get_process_resources")
            metrics["task_id"] = task_id
            metrics["timestamp"] = time.time()
            
            await redis_client.set(f"brownie:heartbeat:{task_id}", json.dumps(metrics), ex=30)
            logger.debug(f"Heartbeat sent for {task_id}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        await asyncio.sleep(5)

@broker.task
async def analysis_task(task_id: str, repo_name: str, issue_number: int, payload: Dict[str, Any]):
    logger.info(f"!!! TASK RECEIVED BY TASKIQ: {task_id} (#{issue_number}) !!!")
    
    try:
        orch = await get_orchestrator()
        
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
        
        logger.info(f"!!! WORKER COMPLETED EXECUTION FOR {task_id} !!!")
    except asyncio.TimeoutError:
        logger.error(f"Worker task TIMEOUT after 600s: {task_id}")
    except Exception as e:
        logger.exception(f"Worker execution failed with error: {e}")
    finally:
        pass

@broker.task
async def execution_task(task_id: str, repo_name: str, issue_number: int, payload: Dict[str, Any]):
    await analysis_task(task_id, repo_name, issue_number, payload)

@broker.task
async def repair_task(task_id: str, repo_name: str, issue_number: int, payload: Dict[str, Any]):
    await analysis_task(task_id, repo_name, issue_number, payload)

@broker.task
async def execute_workflow_task(workflow_name: str, input_data: Any = None):
    """
    指定された動的ワークフローを実行する Taskiq タスク。
    """
    logger.info(f"🚀 Executing dynamic workflow (Taskiq): {workflow_name}")
    try:
        orch = await get_orchestrator()
        loader = WorkflowLoader(Path(orch.project_root))
        tools = loader.load_all()
        
        if workflow_name not in tools:
            logger.error(f"Workflow '{workflow_name}' not found.")
            return

        workflow_func = tools[workflow_name]
        
        await orch._wait_for_llm_ready()
        async with orch.mcp_manager:
            await orch.mcp_manager.start_interpreter_server() # Ensure interpreter is up
            logger.info(f"Running workflow function for {workflow_name}...")
            result = await workflow_func(input_data=input_data)
            logger.info(f"✅ Workflow '{workflow_name}' finished.")
    except Exception as e:
        logger.error(f"Error in execute_workflow_task: {e}")

@broker.task
async def master_trigger_dispatcher():
    """
    1分ごとに起動することを想定したタスク（外部スケジューラから喚起）。
    """
    now = datetime.now()
    logger.debug(f"⏰ Master trigger dispatcher running at {now.isoformat()}")
    
    try:
        orch = await get_orchestrator()
        loader = WorkflowLoader(Path(orch.project_root))
        loader.load_all() 
        
        trigger_manager = WorkflowTriggerManager()
        due_workflows = trigger_manager.get_due_workflows(loader.registry._tools, now)
        
        if not due_workflows:
            return

        for job in due_workflows:
            name = job["workflow_name"]
            logger.info(f"🔔 Trigger matched: '{name}'.")
            
            input_data = {
                "trigger_type": "cron",
                "schedule": job["schedule"],
                "executed_at": now.isoformat()
            }
            
            # Taskiq タスクを投入
            await execute_workflow_task.kiq(name, input_data=input_data)
            
    except Exception as e:
        logger.error(f"Error in master_trigger_dispatcher: {e}")
