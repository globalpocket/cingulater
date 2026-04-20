import asyncio
import json
import os
import resource
from pathlib import Path
from typing import Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger

from src.core.agent import GitHubClientWrapper
from src.core.base import TaskAbortedException
from src.core.config import get_settings
from src.core.mcp_server_manager import MCPServerManager
from src.core.sandbox_manager import SandboxManager
from src.core.state_manager import StateManager

# global_orchestrator は src.core.base に移動しました


class Orchestrator:
    """
    BROWNIE システムの中心的なオーケストレーター。
    インフラ制御は各 MCP サーバーに委譲され、
    本クラスは状態遷移とイベント駆動の指令に専念する。
    """

    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.workspace_base = self.settings.workspace.base_dir
        logger.info(f"Workspace base directory set to: {self.workspace_base}")

        self._increase_max_files()

        # MCP Manager の初期化
        self.mcp_manager = MCPServerManager(self.project_root, config_path=config_path)
        self.gh_client = GitHubClientWrapper(
            self.settings.github.token, mcp_manager=self.mcp_manager
        )
        from src.core.agent import InfrastructureBridge

        self.infra_bridge = InfrastructureBridge(
            self.mcp_manager, token=self.settings.github.token
        )
        # SandboxManager は内部で Testcontainers を使用するよう抽象化済み
        self.sandbox = SandboxManager(
            self.settings.workspace.sandbox_user_id,
            self.settings.workspace.sandbox_group_id,
        )

        # State Manager の初期化 (Redis を内部で使用)
        self.state_manager = StateManager()
        self._workflow_app = None

        # WorkflowLoader の初期化 (Phase 8: 純粋エンジン化)
        from src.core.workflow_manager import WorkflowLoader

        self.workflow_loader = WorkflowLoader(
            Path(self.project_root),
            mcp_manager=self.mcp_manager,
            workspace_root=Path(self.workspace_base),
        )
        # 全てのワークフロー定義 (YAML/MD) をロード
        self.dynamic_workflows = self.workflow_loader.load_all()

        # TriggerManager の初期化 (Phase 10: 規約ベースのディスパッチ)
        from src.core.trigger_manager import WorkflowTriggerManager

        self.trigger_manager = WorkflowTriggerManager(Path(self.project_root))

        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._llm_startup_lock = asyncio.Lock()
        self.is_running = False

        # APScheduler は廃止され、Taskiq Scheduler に統合されました

        self.workspace_base = os.path.expanduser(self.settings.workspace.base_dir)
        os.makedirs(self.workspace_base, exist_ok=True)
        logger.info(f"Workspace base directory set to: {self.workspace_base}")

    def _increase_max_files(self):
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(hard, 8192)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except Exception as e:
            logger.warning(f"Failed to increase file limits: {e}")

    async def start(self):
        """オーケストレーターの起動"""
        logger.info(
            f"Orchestrator starting (Phase 5). Build ID: {self.settings.build_id}"
        )

        async with self.mcp_manager:
            # 必須 MCP サーバーの起動
            await self.mcp_manager.start_github_sdk_server()
            await self.mcp_manager.start_repo_provision_server()
            await self.mcp_manager.start_worker_controller_server()
            await self.mcp_manager.start_task_reasoning_server()
            await self.mcp_manager.start_resource_monitor_server()
            await self.mcp_manager.start_persistence_server()
            await self.mcp_manager.start_intent_interpreter_server()
            await self.mcp_manager.start_governance_server()

            # Worker Server の起動（Taskiq ワーカー & スケジューラのライフサイクル管理）
            worker_client = await self.mcp_manager.start_worker_server()
            await worker_client.call_tool("start_worker")

            self.is_running = True
            logger.info("Taskiq Workers and Scheduler are online.")

            async with self.state_manager as sm:
                # CoderAgent の初期化 (推論ループの管理)
                from src.core.agent import CoderAgent

                self.agent = CoderAgent(
                    config=self.settings.dict(),
                    sandbox=self.sandbox,
                    gh_client=self.gh_client,
                    infra_bridge=self.infra_bridge,
                    mcp_manager=self.mcp_manager,
                    workspace_context=self.workspace_base,
                )

                # ワークフローのコンパイル (司令塔が主導)
                from src.core.graph.builder import compile_workflow

                self._workflow_app = compile_workflow(
                    workflows=self.dynamic_workflows,
                    mcp_manager=self.mcp_manager,
                    checkpointer=sm.saver,
                )

                try:
                    while self.is_running:
                        await asyncio.sleep(1)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    pass
                finally:
                    await self.shutdown()

    async def shutdown(self):
        """オーケストレーターの完全シャットダウン (全滅保証)"""
        logger.info("Initiating Orchestrator shutdown...")
        self.is_running = False

        # 1. HTTP クライアントのクローズ
        await self.http_client.aclose()

        # 2. Sandbox (Docker 等) の停止
        try:
            logger.debug("Stopping sandbox...")
            await asyncio.wait_for(self.sandbox.stop(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Sandbox stop timed out.")
        except Exception as e:
            logger.error(f"Error stopping sandbox: {e}")

        # 3. MCP サーバー群の停止 (AsyncExitStack の aclose を呼び出す)
        try:
            logger.info("Stopping all MCP servers...")
            await asyncio.wait_for(self.mcp_manager.stop_all(), timeout=15.0)
            logger.info("All MCP servers stopped clean.")
        except asyncio.TimeoutError:
            logger.error("MCP servers stop timed out! Some processes might persist.")
        except Exception as e:
            logger.error(f"Error during MCP cleanup: {e}")

        # 4. インフラストラクチャ ブリッジのクリーンアップ
        if hasattr(self, "infra_bridge"):
            await self.infra_bridge.close()

        logger.info("Orchestrator shutdown complete.")

    async def _resource_monitor_loop_job(self):
        try:
            from src.core.workers.pool import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

            redis_client = aioredis.Redis(
                host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD
            )

            keys = await redis_client.keys("brownie:heartbeat:*")
            monitor_client = self.mcp_manager.resource_monitor_client

            for key in keys:
                data = await redis_client.get(key)
                if not data:
                    continue
                hb = json.loads(data)

                # ストール検知 (Resource Monitor MCP に委譲)
                is_stalled = await monitor_client.call_tool(
                    "check_stall",
                    last_heartbeat=hb.get("timestamp", 0),
                    timeout_sec=300,
                )

                if is_stalled:
                    task_id = hb.get("task_id")
                    logger.error(f"Task {task_id} STALLED. Revoking via Worker MCP...")
                    await self.mcp_manager.worker_client.call_tool(
                        "cancel_task", task_id=task_id
                    )
                    await self.update_state(
                        task_id, {"status": "Failed"}, as_node="intent_alignment"
                    )

            await redis_client.aclose()
        except Exception as e:
            logger.error(f"Resource monitor failed: {e}")

    async def _llm_health_loop_job(self):
        """LLM サーバーの死活監視と自動復旧"""
        async with self._llm_startup_lock:
            models_config = [
                ("planner", self.settings.llm.planner_endpoint, 8080),
                ("executor", self.settings.llm.executor_endpoint, 8081),
            ]
            for role, endpoint, port in models_config:
                try:
                    resp = await self.http_client.get(f"{endpoint}/models", timeout=5.0)
                    if resp.status_code == 200:
                        continue
                except Exception as e:
                    logger.warning(f"LLM health check for {role} failed: {e}")

                logger.info(f"LLM Server ({role}) down. Restarting MLX...")
                self._restart_mlx(role, port)

    def _restart_mlx(self, role: str, port: int):
        # 既存のロジックと同様のクリーンアップと Popen を実行
        pass

    async def _execute_task(
        self,
        task_id: str,
        repo_name: str,
        issue_number: int,
        payload: dict,
        executor_func: Optional[Any] = None,
    ):
        """タスクキューのワーカーから呼び出される実行実体"""
        logger.info(f"==> _execute_task STARTED for {task_id} (Issue #{issue_number})")

        # ワーカーはサーバーの準備完了を待つ（起動はメインプロセスに任せる）
        await self._wait_for_llm_ready()

        # ワーカー実行時にその都度チェックポインターを開くことで接続切れを防ぐ
        async with self.state_manager:
            try:
                # 実行直前にもステータスを最終確認
                issue_info = await self.gh_client.get_issue(repo_name, issue_number)
                if issue_info.get("state") != "open":
                    logger.warning(
                        f"Aborting task {task_id} execution: Issue is already CLOSED."
                    )
                    return

                # task_id を含めた入力データを構成
                input_data = payload.copy() if payload else {}
                input_data["task_id"] = task_id
                input_data["repo_name"] = repo_name
                input_data["issue_number"] = issue_number

                # 実行全体に 10 分のタイムアウトを設定
                async def run_workflow():
                    locally_reported = set()  # 同一実行内の重複防止用ローカルメモリ

                    async def astream_gen():
                        config = {"configurable": {"thread_id": task_id}}
                        async for event in self._workflow_app.astream(
                            input_data, config=config
                        ):
                            yield event

                    async for event in astream_gen():
                        # 各イベント後の最新状態を取得
                        current_state = await self.get_state(task_id)
                        state_reported = current_state.get("reported_nodes", [])
                        if not isinstance(state_reported, list):
                            state_reported = []

                        # ローカルの即時記録とDBの状態をマージ
                        reported = set(state_reported).union(locally_reported)

                        for node_name, output in event.items():
                            # 内部イベントの発火 (Phase 11: 自律的な再帰目フック)
                            if self.trigger_manager:
                                await self.trigger_manager.handle_event(
                                    f"on_{node_name}_completed",
                                    {
                                        "node": node_name,
                                        "output": output,
                                        "task_id": task_id,
                                    },
                                    dynamic_workflows=self.dynamic_workflows,
                                    executor_func=executor_func,
                                )

                            if node_name == "intent_alignment" and output.get(
                                "intent_draft"
                            ):
                                if "intent_alignment" not in reported:
                                    locally_reported.add(
                                        "intent_alignment"
                                    )  # 直ちにローカルで記録
                                    draft = output["intent_draft"]
                                    await self.gh_client.post_comment(
                                        repo_name,
                                        issue_number,
                                        f"### 🔍 意図の確認と提案\n\n{draft}"
                                        + self.settings.footer,
                                    )
                                    await self.update_state(
                                        task_id,
                                        {
                                            "reported_nodes": list(
                                                reported.union({"intent_alignment"})
                                            )
                                        },
                                    )

                await asyncio.wait_for(run_workflow(), timeout=600)

                # 最終状態の報告
                final_state = await self.get_state(task_id)
                final_status = final_state.get("status")
                state_reported = final_state.get("reported_nodes", [])
                if not isinstance(state_reported, list):
                    state_reported = []

                # ここでも最新の状態を確認して二重投稿を防止
                final_reported = set(state_reported)

                if (
                    final_status == "WaitingForClarification"
                    and "WaitingForClarification" not in final_reported
                ):
                    plan = final_state.get("plan", "No plan.")
                    await self.gh_client.post_comment(
                        repo_name,
                        issue_number,
                        f"### 🛠 実行計画（承認待ち）\n\n{plan}" + self.settings.footer,
                    )
                    await self.update_state(
                        task_id,
                        {
                            "reported_nodes": list(
                                final_reported.union({"WaitingForClarification"})
                            )
                        },
                    )
                elif final_status == "Completed" and "Completed" not in final_reported:
                    summary = final_state.get("final_summary", "Done.")
                    await self.gh_client.post_comment(
                        repo_name,
                        issue_number,
                        f"### ✅ 完了報告\n\n{summary}" + self.settings.footer,
                    )
                    await self.update_state(
                        task_id,
                        {"reported_nodes": list(final_reported.union({"Completed"}))},
                    )

            except asyncio.TimeoutError:
                logger.error(f"Task execution TIMEOUT: {task_id}")
                err_msg = (
                    "### ⚠️ タイムアウトによる中断\n"
                    "ワークフローが一定時間内に完了しませんでした。"
                    "特定の処理でループが発生したか、"
                    "LLM の応答が停止した可能性があります。"
                )
                await self.gh_client.post_comment(
                    repo_name,
                    issue_number,
                    err_msg + self.settings.footer,
                )
                await self.update_state(
                    task_id, {"status": "Failed"}, as_node="intent_alignment"
                )
            except TaskAbortedException as tae:
                logger.warning(f"Task {task_id} aborted by gate: {tae}")
                # ユーザーが意図的にクローズしたため、
                # Failed ではなく Skipped またはそのまま終了
                await self.update_state(
                    task_id, {"status": "Aborted"}, as_node="intent_alignment"
                )
            except Exception as e:
                logger.error(f"Task execution error: {e}", exc_info=True)
                # ルール 4 に基づき、エラー情報を簡潔に報告
                error_msg = str(e)
                err_msg = (
                    f"### ❌ 実行エラーが発生しました\n\n原因: {error_msg}\n"
                    "内部的な問題により処理を継続できないか、"
                    "リソースが不足しています。"
                )
                await self.gh_client.post_comment(
                    repo_name,
                    issue_number,
                    err_msg + self.settings.footer,
                )
                await self.update_state(
                    task_id, {"status": "Failed"}, as_node="intent_alignment"
                )

    async def get_state(self, thread_id: str) -> dict:
        """thread_id から最新の状態を取得する"""
        if not self._workflow_app:
            return {}
        config = {"configurable": {"thread_id": thread_id}}
        state = await self._workflow_app.aget_state(config)
        return state.values if state else {}

    async def update_state(
        self, thread_id: str, values: dict, as_node: Optional[str] = None
    ):
        """thread_id の状態を更新する"""
        if not self._workflow_app:
            return
        config = {"configurable": {"thread_id": thread_id}}
        return await self._workflow_app.aupdate_state(config, values, as_node=as_node)

    async def _wait_for_llm_ready(self):
        """LLM サーバーが利用可能になるまで待機する"""
        async with self._llm_startup_lock:
            while True:
                try:
                    # プランナーが応答するかチェック
                    resp = await self.http_client.get(
                        f"{self.settings.llm.planner_endpoint}/models", timeout=2.0
                    )
                    if resp.status_code == 200:
                        break
                except Exception as e:
                    logger.debug(f"LLM readiness check failed (expected): {e}")
                logger.info("Waiting for LLM servers to be online...")
                await asyncio.sleep(5)
