import asyncio
import os
import sys
import logging
import yaml
import time
import subprocess
import httpx
from typing import Optional, Dict, Any, List
from datetime import datetime

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.core.worker_pool import WorkerPool
from src.core.agent import CoderAgent, TaskAbortedException
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
        
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._llm_startup_lock = asyncio.Lock()
        self.is_running = False

        # ワークスペースのベースパスを解決 (設計書 8.1 参照)
        ws_config = self.config.get('workspace', {})
        base_dir = ws_config.get('base_dir', "~/.local/share/brownie/workspaces")
        self.workspace_base = os.path.expanduser(base_dir)
        os.makedirs(self.workspace_base, exist_ok=True)
        logger.info(f"Workspace base directory set to: {self.workspace_base}")

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

        self.is_running = True
        # LLM サーバーの自動起動監視を開始
        asyncio.create_task(self._llm_health_loop())

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
        await self.http_client.aclose()
        self.worker_pool.stop()
        await self.mcp_manager.stop_all()
        logger.info("Orchestrator cleanup completed.")

    async def _llm_health_loop(self):
        """LLM サーバーの死活監視ループ"""
        while self.is_running:
            try:
                await self._check_llm_health()
            except Exception as e:
                logger.error(f"Error in LLM health loop: {e}")
            await asyncio.sleep(60) # 1分ごとにチェック

    async def _check_llm_health(self):
        """LLM サーバーの死活監視と自動起動"""
        async with self._llm_startup_lock:
            models_config = [
                ("planner", self.config['llm']['planner_endpoint'], 8080),
                ("executor", self.config['llm']['executor_endpoint'], 8081)
            ]
            
            for role, endpoint, port in models_config:
                try:
                    resp = await self.http_client.get(f"{endpoint}/models", timeout=5.0)
                    if resp.status_code == 200:
                        continue
                except Exception:
                    pass
                
                model_name = self.config['llm']['models'].get(role)
                logger.info(f"LLM Server ({role}) down on port {port}. Restarting MLX: {model_name}")
                
                # ポートに基づいた特定プロセスのクリーンアップ
                try:
                    result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False)
                    pids = result.stdout.strip().split("\n")
                    for pid in pids:
                        if pid.strip():
                            logger.info(f"Killing stale process {pid} using port {port}")
                            subprocess.run(["kill", "-9", pid], check=False)
                except Exception as e:
                    logger.warning(f"Failed to cleanup processes on port {port}: {e}")

                # MLX サーバーの再起動
                env = os.environ.copy()
                model_dir = self.config.get('llm', {}).get('model_dir', '~/.local/share/brownie/models')
                env["HF_HOME"] = os.path.expanduser(model_dir)
                
                venv_python = os.path.join(self.project_root, ".venv", "bin", "python")
                if not os.path.exists(venv_python):
                    venv_python = sys.executable

                subprocess.Popen(
                    [venv_python, "-m", "mlx_lm.server", "--model", model_name, "--port", str(port)], 
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
                    start_new_session=True, env=env
                )
                logger.info(f"MLX Server ({role}) for {model_name} starting on port {port}...")

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
                    await self._queue_task(task_id, target_repo, m['number'], comment_id=str(m['comment_id']), comment_body=m.get('body'))
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

    async def _queue_task(self, task_id: str, repo_name: str, issue_number: int, comment_id: Optional[str] = None, comment_body: Optional[str] = None):
        config = {"configurable": {"thread_id": task_id}}
        
        # 投入前に最新の Issue ステータスを確認 (設計改善: クローズ済みはスキップ)
        issue_info = await self.gh_client.get_issue(repo_name, issue_number)
        if issue_info.get("state") != "open":
            logger.info(f"Skipping task {task_id} because the issue is CLOSED.")
            # 必要であれば通知を既読にするなどの処理
            return

        state = await self._workflow_app.aget_state(config)
        
        payload_to_send = None
        if state.values:
            status = state.values.get("status")
            # 実行中（InProgress/InQueue）かつ指示が変わっていない場合はスキップ
            if status in ['InProgress', 'InQueue'] and state.values.get("resume_comment_id") == comment_id:
                return
            
            # ユーザー待機中（Waiting...）または新規指示の場合は再投入
            logger.info(f"Re-queueing task {task_id} with new status/comment.")
            
            new_state = {"resume_comment_id": comment_id, "status": "InQueue"}
            if comment_body:
                # ユーザーの追加コメントを指示にマージしてエージェントに伝える
                existing_instruction = state.values.get("instruction", "")
                timestamp = datetime.utcnow().isoformat()
                new_state["instruction"] = existing_instruction + f"\n\n[USER UPDATE @ {timestamp}]:\n{comment_body}"
            
            await self._workflow_app.aupdate_state(config, new_state, as_node="intent_alignment")
            updated_state = await self._workflow_app.aget_state(config)
            payload_to_send = updated_state.values
        else:
            repo_path = os.path.join(self.workspace_base, repo_name.split("/")[-1])
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
                # 実行直前にもステータスを最終確認
                issue_info = await self.gh_client.get_issue(repo_name, issue_number)
                if issue_info.get("state") != "open":
                    logger.warning(f"Aborting task {task_id} execution: Issue is already CLOSED.")
                    return

                # task_id を含めた入力データを構成
                input_data = payload.copy() if payload else {}
                input_data["task_id"] = task_id
                input_data["repo_name"] = repo_name
                input_data["issue_number"] = issue_number
                
                # 実行全体に 10 分のタイムアウトを設定
                async def run_workflow():
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
                
                await asyncio.wait_for(run_workflow(), timeout=600)

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

            except asyncio.TimeoutError:
                logger.error(f"Task execution TIMEOUT: {task_id}")
                await self.gh_client.post_comment(repo_name, issue_number, "### ⚠️ タイムアウトによる中断\n処理時間が制限（10分）を超えたため、安全のために実行を中断しました。特定の処理でループが発生したか、LLM の応答が停止した可能性があります。" + get_footer())
                await workflow_app.aupdate_state(config, {"status": "Failed"}, as_node="intent_alignment")
            except TaskAbortedException as tae:
                logger.warning(f"Task {task_id} aborted by gate: {tae}")
                # ユーザーが意図的にクローズしたため、Failed ではなく Skipped またはそのまま終了
                await workflow_app.aupdate_state(config, {"status": "Aborted"}, as_node="intent_alignment")
            except Exception as e:
                logger.error(f"Task execution error: {e}", exc_info=True)
                await workflow_app.aupdate_state(config, {"status": "Failed"}, as_node="intent_alignment")
