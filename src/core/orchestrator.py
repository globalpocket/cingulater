import asyncio
import os
import sys
import logging
import yaml
import time
from typing import Optional, Dict, Any, List
from datetime import datetime

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.core.worker_pool import WorkerPool
from src.gh_platform.client import GitHubClientWrapper, GitHubRateLimitException
from src.workspace.sandbox import SandboxManager
from src.mcp_server.manager import MCPServerManager
from src.core.persistence import PersistenceManager
from src.version import get_footer, get_build_id

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, config_path: str):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Persistence Manager の初期化
        db_path = self.config['database']['db_path']
        self.persistence = PersistenceManager(db_path)
        
        self.worker_pool = WorkerPool(self.project_root)
        self.gh_client = GitHubClientWrapper(os.getenv("GITHUB_TOKEN", ""), persistence=self.persistence)
        self.sandbox = SandboxManager(self.config['workspace']['sandbox_user_id'], 
                                     self.config['workspace']['sandbox_group_id'])
        self.mcp_manager = MCPServerManager(self.project_root)

        # 設定ファイルにあるリポジトリを初期登録
        initial_repos = self.config['agent'].get('repositories', [])
        for repo in initial_repos:
            self.persistence.upsert_repository(repo)

        # ワークフローの準備 (checkpoint なしで一度準備)
        from src.core.graph.builder import compile_workflow
        self._workflow_app = compile_workflow()

    async def start(self):
        """オーケストレーター（メンション監視プロセス）の起動"""
        logger.info(f"Orchestrator starting. Build ID: {get_build_id()}")
        
        checkpoint_path = os.path.join(self.project_root, ".brwn", "checkpoints.db")
        os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
        
        global global_orchestrator
        global_orchestrator = self
        
        logger.info("Starting WorkerPool...")
        await self.worker_pool.run()
        
        logger.info("BOOT SEQUENCE COMPLETED. Entering polling loop.")

        # メインプロセスではチェックポインターを維持し続ける
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
            from src.core.graph.builder import compile_workflow
            self._workflow_app = compile_workflow(checkpointer=checkpointer)
            
            try:
                self.is_running = True
                while self.is_running:
                    try:
                        await self._poll_mentions()
                        logger.debug("Polling cycle completed successfully.")
                        await asyncio.sleep(self.config['agent']['polling_interval_sec'])
                    except Exception as e:
                        logger.error(f"Unexpected error in polling loop: {e}")
                        await asyncio.sleep(30)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Orchestrator stopping...")
            finally:
                await self.shutdown()

    async def shutdown(self):
        """オーケストレーターのクリーンアップ"""
        logger.info("Orchestrator shutting down...")
        self.is_running = False
        await self.mcp_manager.stop_all()
        logger.info("Orchestrator cleanup completed.")

    async def _poll_mentions(self):
        """メンション取得とキュー投入"""
        try:
            exclude_list = self.config['agent'].get('exclude_repositories', [])
            all_mentions = await self.gh_client.get_mentions_to_process()
            
            for m in all_mentions:
                target_repo = m['repo_name']
                if target_repo in exclude_list:
                    continue
                    
                task_id = f"{target_repo}#{m['number']}"
                body = m.get('body', '').lower()
                
                if "/approve" in body:
                    await self._resume_workflow(task_id, "Approve")
                elif "/reject" in body:
                    await self._resume_workflow(task_id, "Reject")
                else:
                    await self._queue_task(task_id, target_repo, m['number'], comment_id=str(m['comment_id']))
        except Exception as e:
            logger.error(f"Polling error: {e}")

    async def _resume_workflow(self, task_id: str, decision: str):
        # データベースパスの取得
        checkpoint_path = os.path.join(self.project_root, ".brwn", "checkpoints.db")
        
        # チェックポインターを明示的に起動して状態を更新
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
            from src.core.graph.builder import compile_workflow
            workflow_app = compile_workflow(checkpointer=checkpointer)
            
            config = {"configurable": {"thread_id": task_id}}
            await workflow_app.aupdate_state(
                config, 
                {"governance_decision": decision, "status": "InQueue"}, 
                as_node="intent_alignment"
            )
            await self.worker_pool.add_task(task_id, 1, task_id.split("#")[0], int(task_id.split("#")[1]))

    async def _queue_task(self, task_id: str, repo_name: str, issue_number: int, comment_id: Optional[str] = None):
        config = {"configurable": {"thread_id": task_id}}
        state = await self._workflow_app.aget_state(config)
        
        payload_to_send = None
        if state.values:
            status = state.values.get("status")
            # 実行中（InProgress/InQueue）かつ指示が変わっていない場合はスキップ
            if status in ['InProgress', 'InQueue'] and state.values.get("resume_comment_id") == comment_id:
                return
            
            # ユーザー待機中（Waiting...）または新規指示の場合は再投入
            logger.info(f"Re-queueing task {task_id} with new status/comment.")
            await self._workflow_app.aupdate_state(config, {"resume_comment_id": comment_id, "status": "InQueue"}, as_node="intent_alignment")
            payload_to_send = state.values
        else:
            repo_path = os.path.join(self.project_root, "workspaces", repo_name.split("/")[-1])
            issue = await self.gh_client.get_issue(repo_name, issue_number)
            initial_values = {
                "task_id": task_id, "thread_id": task_id, "repo_name": repo_name,
                "repo_path": repo_path, "issue_number": issue_number,
                "instruction": f"Title: {issue.get('title','')}\n\nBody:\n{issue.get('body','')}",
                "status": "Phase0_WaitingForUserConfirmation",
                "intent_confirmed": False,
                "history": [], "metadata": {},
                "reported_nodes": [],
                "trigger_comment_id": comment_id, "created_at": datetime.utcnow().isoformat()
            }
            await self._workflow_app.aupdate_state(config, initial_values)
            payload_to_send = initial_values

        await self.worker_pool.add_task(task_id, 1, repo_name, issue_number, payload=payload_to_send)

    async def _execute_task(self, task_id: str, repo_name: str, issue_number: int, payload: dict = None):
        """Huey ワーカーから呼び出される実行実体（ワーカープロセス内）"""
        checkpoint_path = os.path.join(self.project_root, ".brwn", "checkpoints.db")
        
        # ワーカー実行時にその都度チェックポインターを開くことで接続切れを防ぐ
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
            from src.core.graph.builder import compile_workflow
            workflow_app = compile_workflow(checkpointer=checkpointer)
            
            config = {"configurable": {"thread_id": task_id}}
            state = await workflow_app.aget_state(config)

            try:
                # task_id を含めた入力データを構成
                input_data = payload.copy() if payload else {}
                input_data["task_id"] = task_id
                input_data["repo_name"] = repo_name
                input_data["issue_number"] = issue_number
                
                async for event in workflow_app.astream(input_data, config=config):
                    # 各イベント後の最新状態を取得
                    current_state = await workflow_app.aget_state(config)
                    reported = current_state.values.get("reported_nodes", [])

                    for node_name, output in event.items():
                        if node_name == "intent_alignment" and "intent_draft" in output:
                            if "intent_alignment" not in reported:
                                draft = output["intent_draft"]
                                await self.gh_client.post_comment(repo_name, issue_number, f"### 🔍 意図の確認と提案\n\n{draft}" + get_footer())
                                await workflow_app.aupdate_state(config, {"reported_nodes": ["intent_alignment"]})
                        
                        elif node_name == "core_analysis" and output.get("status") == "Phase1_Completed":
                            if "core_analysis" not in reported:
                                await self.gh_client.post_comment(repo_name, issue_number, "### 📊 全方位分析完了\nリポジトリの解析が完了しました。" + get_footer())
                                await workflow_app.aupdate_state(config, {"reported_nodes": ["core_analysis"]})

                # 最終状態の報告
                final_state = await workflow_app.aget_state(config)
                final_status = final_state.values.get("status")
                reported = final_state.values.get("reported_nodes", [])
                
                if final_status == "WaitingForClarification" and "WaitingForClarification" not in reported:
                    plan = final_state.values.get("plan", "No plan.")
                    await self.gh_client.post_comment(repo_name, issue_number, f"### 🛠 実行計画（承認待ち）\n\n{plan}" + get_footer())
                    await workflow_app.aupdate_state(config, {"reported_nodes": ["WaitingForClarification"]})
                elif final_status == "Completed" and "Completed" not in reported:
                    summary = final_state.values.get("final_summary", "Done.")
                    await self.gh_client.post_comment(repo_name, issue_number, f"### ✅ 完了報告\n\n{summary}" + get_footer())
                    await workflow_app.aupdate_state(config, {"reported_nodes": ["Completed"]})

            except Exception as e:
                logger.error(f"Task execution error: {e}", exc_info=True)
                await workflow_app.aupdate_state(config, {"status": "Failed"}, as_node="intent_alignment")
