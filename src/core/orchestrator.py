import asyncio
import os
import sys
import logging
import yaml
import subprocess
import httpx
from typing import Optional
import json
import resource
from datetime import datetime

from src.core.state_manager import StateManager
from src.core.worker_pool import WorkerPool
from src.core.agent import TaskAbortedException
from src.gh_platform.client import GitHubClientWrapper
from src.core.sandbox_manager import SandboxManager
from src.core.mcp_server_manager import MCPServerManager
from src.core.persistence import PersistenceManager
from src.version import get_footer, get_build_id
from src.utils.config_loader import get_config
from src.llm.robust_model import wait_for_llm_ready

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config_path: str):
        self.config = get_config(config_path)

        # magicvalues.yaml による例外的な上書き (設計書 11.2)
        magic_path = os.path.join(
            os.path.dirname(config_path or ""), "magicvalues.yaml"
        )
        if os.path.exists(magic_path):
            try:
                with open(magic_path, "r") as f:
                    magic_config = yaml.safe_load(f)
                if magic_config:
                    self._deep_merge(magic_config, self.config)
                    logger.info(f"Magic values loaded and merged from {magic_path}")
            except Exception as e:
                logger.warning(f"Failed to load magicvalues.yaml: {e}")

        # リソースの初期化 (設計書 3.2)
        self.project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        # ファイルディスクリプタ制限の拡張試行
        self._increase_max_files()

        # Persistence Manager の初期化
        db_path = self.config["database"]["db_path"]
        self.persistence = PersistenceManager(db_path)

        self.worker_pool = WorkerPool(self.project_root)
        self.gh_client = GitHubClientWrapper(
            os.getenv("GITHUB_TOKEN", ""), persistence=self.persistence
        )
        self.sandbox = SandboxManager(
            self.config["workspace"]["sandbox_user_id"],
            self.config["workspace"]["sandbox_group_id"],
        )
        self.mcp_manager = MCPServerManager(self.project_root, config_path=config_path)

        # State Manager の初期化
        checkpoint_path = os.path.join(self.project_root, ".brwn", "checkpoints.db")
        self.state_manager = StateManager(checkpoint_path)

        # 設定ファイルにあるリポジトリを初期登録
        initial_repos = self.config["agent"].get("repositories", [])
        for repo in initial_repos:
            self.persistence.upsert_repository(repo)

        # ワークフローの準備 (checkpoint なしで一度準備)
        self._workflow_app = self.state_manager.workflow_app

        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._llm_startup_lock = asyncio.Lock()
        self.is_running = False

        # ワークスペースのベースパスを解決 (設計書 8.1 参照)
        ws_config = self.config.get("workspace", {})
        base_dir = ws_config.get("base_dir", "~/.local/share/brownie/workspaces")
        self.workspace_base = os.path.expanduser(base_dir)
        os.makedirs(self.workspace_base, exist_ok=True)
        logger.info(f"Workspace base directory set to: {self.workspace_base}")

    def _increase_max_files(self):
        """ファイルディスクリプタ（ulimit -n）の制限を拡張する"""
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            logger.info(f"Current File Limits: soft={soft}, hard={hard}")
            
            # macOS 等で極端に低い場合があるため、可能な限り引き上げる (4096 は安全圏)
            target = min(hard, 8192)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                new_soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
                logger.info(f"Updated File Limit (soft): {new_soft}")
        except Exception as e:
            logger.warning(f"Failed to increase file limits: {e}")

    def _deep_merge(self, source, destination):
        """辞書を再帰的にマージする"""
        for key, value in source.items():
            if (
                isinstance(value, dict)
                and key in destination
                and isinstance(destination[key], dict)
            ):
                self._deep_merge(value, destination[key])
            else:
                destination[key] = value

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

        # 実行全体を MCPServerManager のコンテキストで包む
        async with self.mcp_manager:
            # Resource Monitor Server の起動
            await self.mcp_manager.start_resource_monitor_server()

            self.is_running = True
            # LLM サーバーの自動起動監視を開始
            asyncio.create_task(self._llm_health_loop())

            # メインプロセスではチェックポインターを維持し続ける
            async with self.state_manager as sm:
                self._workflow_app = sm.workflow_app

                try:
                    while self.is_running:
                        try:
                            # ワーカープロセスの生存確認と自動復旧
                            await self.worker_pool.check_health()

                        # リソース監視とストール検知（初回のみ起動）
                        if (
                            not hasattr(self, "resource_monitor_task")
                            or self.resource_monitor_task.done()
                        ):
                            self.resource_monitor_task = asyncio.create_task(
                                self._resource_monitor_loop()
                            )

                        await self._poll_mentions()
                        logger.debug("Polling cycle completed successfully.")
                        await asyncio.sleep(
                            self.config["agent"]["polling_interval_sec"]
                        )
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
            await asyncio.sleep(60)  # 1分ごとにチェック

    async def _resource_monitor_loop(self):
        """Worker のリソース状況を監視し、ストールを検知するループ"""
        client = self.mcp_manager.resource_monitor_client
        if not client:
            logger.error("Resource Monitor Client is not initialized.")
            return

        while self.is_running:
            try:
                # Redis からすべての Heartbeat を取得
                conn = self.worker_pool.huey.storage.conn
                keys = conn.keys("brownie:heartbeat:*")

                for key in keys:
                    data = conn.get(key)
                    if not data:
                        continue
                    hb = json.loads(data)

                    task_id = hb.get("task_id")
                    last_seen = hb.get("timestamp", 0)
                    cpu_usage = hb.get("cpu_pct", 0.0)

                    # ストール判定 (5分以上進捗がなく、かつ CPU がアイドルの場合)
                    # MCP 経由で判定。Worker プロセスのメトリクスは Redis にある hb['cpu_pct'] 等を使うため、
                    # ここでは単純な timeout 判定＋システム状況（または Worker PID が分かればそのPID）での stall 判定を行う。
                    # 元の ResourceGuardian.check_for_stall を踏襲し、MCP 側の check_stall ツールを呼ぶ。
                    is_stalled = await client.call_tool(
                        "check_stall", 
                        last_heartbeat=last_seen, 
                        timeout_sec=300,
                        pid=hb.get("pid") # Heartbeat に PID が含まれていることを期待
                    )
                    
                    if is_stalled:
                        logger.error(
                            f"Intelligent Stall Detection: Task {task_id} looks HUNG (CPU: {cpu_usage}%). Revoking and restarting worker."
                        )

                        # 1. タスクを取り消し
                        self.worker_pool.revoke_task(task_id)

                        # 2. Worker を再起動 (古いプロセスを掃除)
                        self.worker_pool.stop()
                        await self.worker_pool.run()

                        # 3. GitHub に通知
                        repo, num = task_id.split("#")
                        await self.gh_client.post_comment(
                            repo,
                            int(num),
                            "### ⚠️ 異常検知による自動再起動\n処理が長時間停止し、CPU負荷も確認できなかったため、タスクを中断して Worker を再起動しました。",
                        )

                        # 4. DB ステータスを更新
                        await self.state_manager.update_state(
                            task_id, {"status": "Failed"}, as_node="intent_alignment"
                        )

            except Exception as e:
                logger.error(f"Error in resource monitor loop: {e}")

            await asyncio.sleep(30)  # 30秒ごとに監視

    async def _check_llm_health(self):
        """LLM サーバーの死活監視と自動起動"""
        async with self._llm_startup_lock:
            models_config = [
                ("planner", self.config["llm"]["planner_endpoint"], 8080),
                ("executor", self.config["llm"]["executor_endpoint"], 8081),
            ]

            for role, endpoint, port in models_config:
                try:
                    resp = await self.http_client.get(f"{endpoint}/models", timeout=5.0)
                    if resp.status_code == 200:
                        continue
                except Exception:
                    pass

                model_name = self.config["llm"]["models"].get(role)
                logger.info(
                    f"LLM Server ({role}) down on port {port}. Restarting MLX: {model_name}"
                )

                # ポートに基づいた特定プロセスのクリーンアップ
                try:
                    result = subprocess.run(
                        ["lsof", "-ti", f":{port}"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    pids = result.stdout.strip().split("\n")
                    my_pid = str(os.getpid())
                    worker_pid = (
                        str(self.worker_pool.consumer_proc.pid)
                        if self.worker_pool.consumer_proc
                        else None
                    )

                    for pid in pids:
                        target_pid = pid.strip()
                        if not target_pid:
                            continue

                        # 自分自身やワーカーを殺さないようにガード
                        if target_pid == my_pid:
                            logger.debug(
                                f"Skipping self-kill for PID {target_pid} on port {port}"
                            )
                            continue
                        if worker_pid and target_pid == worker_pid:
                            logger.debug(
                                f"Skipping worker-kill for PID {target_pid} on port {port}"
                            )
                            continue

                        logger.info(
                            f"Killing stale process {target_pid} using port {port}"
                        )
                        subprocess.run(["kill", "-9", target_pid], check=False)
                except Exception as e:
                    logger.warning(f"Failed to cleanup processes on port {port}: {e}")

                # MLX サーバーの再起動
                env = os.environ.copy()
                model_dir = self.config.get("llm", {}).get(
                    "model_dir", "~/.local/share/brownie/models"
                )
                env["HF_HOME"] = os.path.expanduser(model_dir)

                venv_python = os.path.join(self.project_root, ".venv", "bin", "python")
                if not os.path.exists(venv_python):
                    venv_python = sys.executable

                # ログファイルの準備
                log_file_path = os.path.join(
                    self.project_root, "logs", f"mlx_{role}.log"
                )
                log_file = open(log_file_path, "a")

                # サーバーモジュールの決定 (Gemma 4 や Vision モデルの場合は mlx-vlm を使用)
                server_module = "mlx_lm.server"
                logger.info(f"DEBUG: Evaluating model selection for: '{model_name}'")
                if "gemma-4" in model_name.lower() or "vision" in model_name.lower():
                    server_module = "mlx_vlm.server"
                    logger.info(
                        f"Using multimodal server ({server_module}) for {model_name}"
                    )
                else:
                    logger.info(
                        f"DEBUG: Selected default server ({server_module}) for {model_name}"
                    )

                try:
                    subprocess.Popen(
                        [
                            venv_python,
                            "-m",
                            server_module,
                            "--model",
                            model_name,
                            "--port",
                            str(port),
                        ],
                        stdout=log_file,
                        stderr=log_file,
                        start_new_session=True,
                        env=env,
                    )
                finally:
                    # 子プロセスに FD が引き継がれた後は、親プロセス側では閉じてよい
                    log_file.close()

                logger.info(
                    f"MLX Server ({role}) for {model_name} starting on port {port} using {server_module}... (Log: {log_file_path})"
                )

                # サーバーが準備完了になるまで待機
                timeout_sec = self.config.get("llm", {}).get("timeout_sec", 180)
                logger.info(
                    f"Waiting for MLX Server ({role}) to be ready on port {port} (Max {timeout_sec}s)..."
                )
                start_time = asyncio.get_event_loop().time()
                is_ready = False
                while asyncio.get_event_loop().time() - start_time < timeout_sec:
                    try:
                        # 指数的なバックオフではなく、1秒間隔でチェック
                        resp = await self.http_client.get(
                            f"{endpoint}/models", timeout=2.0
                        )
                        if resp.status_code == 200:
                            is_ready = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                if is_ready:
                    logger.info(f"MLX Server ({role}) is READY!")
                else:
                    logger.error(
                        f"MLX Server ({role}) FAILED to become ready within 60s."
                    )

    async def _wait_for_llm_ready(self):
        """ワーカープロセス用：LLM サーバーが準備完了になるまで待機する"""
        logger.info(
            "Worker is waiting for LLM servers (planner & executor) to be ready..."
        )

        planner_ready = await wait_for_llm_ready(self.config["llm"]["planner_endpoint"])
        executor_ready = await wait_for_llm_ready(
            self.config["llm"]["executor_endpoint"]
        )

        if planner_ready and executor_ready:
            logger.info("Worker confirmed ALL LLM servers are READY!")
            return True
        return False

        logger.error("Worker timed out waiting for LLM servers to be ready.")
        return False

    async def _poll_mentions(self):
        """メンション取得とキュー投入"""
        try:
            exclude_list = self.config["agent"].get("exclude_repositories", [])
            all_mentions = await self.gh_client.get_mentions_to_process()

            # 1. メンションを Task ID (Issue) ごとに整理し、最新のもののみを残す (デデュープ)
            latest_mentions_per_task = {}
            for m in all_mentions:
                task_id = f"{m['repo_name']}#{m['number']}"
                if m["repo_name"] in exclude_list:
                    continue

                if task_id not in latest_mentions_per_task:
                    latest_mentions_per_task[task_id] = m
                else:
                    # updated_at で比較して最新を保持
                    cur_ts = m.get("updated_at", "")
                    old_ts = latest_mentions_per_task[task_id].get("updated_at", "")
                    if cur_ts > old_ts:
                        latest_mentions_per_task[task_id] = m

            # 2. 最新のメンションのみを処理
            for task_id, m in latest_mentions_per_task.items():
                target_repo = m["repo_name"]
                mention_id = str(m.get("comment_id", f"body_{m['number']}"))
                updated_at = m.get("updated_at", "1970-01-01T00:00:00Z")

                # 重複排除と編集検知のチェック (Persistence)
                status = self.persistence.is_mention_new_or_updated(
                    mention_id, updated_at
                )
                if status == "UNCHANGED":
                    # 未読だが DB 上は処理済みの場合は、再検知を防ぐために既読化
                    logger.debug(
                        f"Mention {mention_id} already in DB. Marking as read."
                    )
                    await self.gh_client.mark_issue_notifications_as_read(
                        target_repo, m["number"]
                    )
                    continue

                body = m.get("body", "").lower()
                logger.info(f"Detected {status} mention: {task_id} (ID: {mention_id})")

                # 更新（指示の編集）の場合は既存タスクをキャンセル
                if status == "UPDATED":
                    logger.info(
                        f"Detected mention update for {mention_id}. Revoking current tasks for {task_id}."
                    )
                    self.worker_pool.revoke_task(task_id)

                # キュー投入（または再開）
                success = False
                if "/approve" in body:
                    await self._resume_workflow(task_id, "Approve")
                    success = True
                elif "/reject" in body:
                    await self._resume_workflow(task_id, "Reject")
                    success = True
                else:
                    success = await self._queue_task(
                        task_id,
                        target_repo,
                        m["number"],
                        comment_id=mention_id,
                        comment_body=m.get("body"),
                    )

                # 投入に成功した場合は、DB を更新し、GitHub 通知を既読にする
                if success:
                    self.persistence.save_processed_mention(m)
                    await self.gh_client.mark_issue_notifications_as_read(
                        target_repo, m["number"]
                    )
        except Exception as e:
            logger.error(f"Polling error: {e}")

    async def _resume_workflow(self, task_id: str, decision: str):
        # チェックポインターを明示的に起動して状態を更新
        async with self.state_manager:
            await self.state_manager.update_state(
                task_id,
                {"governance_decision": decision, "status": "InQueue"},
                as_node="intent_alignment",
            )
            await self.worker_pool.add_task(
                task_id, 1, task_id.split("#")[0], int(task_id.split("#")[1])
            )

    async def _queue_task(
        self,
        task_id: str,
        repo_name: str,
        issue_number: int,
        comment_id: Optional[str] = None,
        comment_body: Optional[str] = None,
    ):


        # 重複投入ガード: 同一 Task ID が既にアクティブな場合はスキップ
        if task_id in self.worker_pool.active_tasks:
            logger.info(
                f"Task {task_id} is already ACTIVE. Skipping redundant queueing."
            )
            return True  # 既に存在するため「処理中」として扱う

        # 資源の安全性を確認 (Resource Guardian MCP)
        client = self.mcp_manager.resource_monitor_client
        if client:
            is_safe = await client.call_tool("is_system_safe", min_available_gb=4.0)
            if not is_safe:
                logger.warning(
                    f"Resource Guardian: System memory is low. Delaying task queueing for {task_id} until next poll."
                )
                return False
        else:
            logger.warning("Resource Monitor Client not ready. Proceeding with caution.")

        # 投入前に最新の Issue ステータスを確認 (設計改善: クローズ済みはスキップ)
        issue_info = await self.gh_client.get_issue(repo_name, issue_number)
        if issue_info.get("state") != "open":
            logger.info(
                f"Skipping task {task_id} because the issue is CLOSED. Marking notifications as read."
            )
            # 通知を既読にする
            await self.gh_client.mark_issue_notifications_as_read(
                repo_name, issue_number
            )
            return True

        state = await self.state_manager.get_state(task_id)

        payload_to_send = None
        if state.values:
            status = state.values.get("status")
            # 実行中（InProgress/InQueue）または、失敗済み（Failed）かつ指示に更新がない場合はスキップ
            is_active = status in ["InProgress", "InQueue"]
            is_failed_no_update = (
                status == "Failed"
                and state.values.get("resume_comment_id") == comment_id
            )

            if (is_active or is_failed_no_update) and state.values.get(
                "resume_comment_id"
            ) == comment_id:
                if status == "Failed":
                    logger.debug(
                        f"Task {task_id} is in Failed state with no new instructions. Skipping automatic retry."
                    )
                return True

            # ユーザー待機中（Waiting...）または新規指示の場合は再投入
            logger.info(
                f"Re-queueing task {task_id} (Current Status: {status}) due to new comment or state change."
            )

            new_state = {"resume_comment_id": comment_id, "status": "InQueue"}

            # --- 報告フラグのリセットと指示の整理 ---
            # 1. 報告済みフラグから intent_alignment を削除して、新しい提案を投稿できるようにする
            reported = state.values.get("reported_nodes", [])
            if "intent_alignment" in reported:
                new_reported = [node for node in reported if node != "intent_alignment"]
                new_state["reported_nodes"] = new_reported
                logger.info(
                    f"Reset 'intent_alignment' from reported_nodes to allow re-posting for {task_id}"
                )

            # 2. 指示文のマージとクリーンアップ (過去の [USER UPDATE] 蓄積を抑制)
            if comment_body:
                existing_instruction = state.values.get("instruction", "")
                timestamp = datetime.utcnow().isoformat()

                # もし過去の [USER UPDATE] が多すぎる場合は、元の Body と最新の Update のみに絞る
                if existing_instruction.count("[USER UPDATE @") > 2:
                    # 最初（元のBody）と最後（前回のUpdate）を残すロジック（簡易版）
                    parts = existing_instruction.split("[USER UPDATE @")
                    base_instruction = parts[0].strip()
                    new_state["instruction"] = (
                        base_instruction
                        + f"\n\n[LATEST USER UPDATE @ {timestamp}]:\n{comment_body}"
                    )
                else:
                    new_state["instruction"] = (
                        existing_instruction
                        + f"\n\n[USER UPDATE @ {timestamp}]:\n{comment_body}"
                    )

            await self.state_manager.update_state(
                task_id, new_state, as_node="intent_alignment"
            )
            updated_state = await self.state_manager.get_state(task_id)
            payload_to_send = updated_state.values
        else:
            repo_path = os.path.join(self.workspace_base, repo_name.split("/")[-1])
            issue = await self.gh_client.get_issue(repo_name, issue_number)
            initial_values = {
                "task_id": task_id,
                "thread_id": task_id,
                "repo_name": repo_name,
                "repo_path": repo_path,
                "issue_number": issue_number,
                "instruction": f"Title: {issue.get('title','')}\n\nBody:\n{issue.get('body','')}",
                "status": "Phase0_WaitingForUserConfirmation",
                "intent_confirmed": False,
                "history": [],
                "metadata": {},
                "reported_nodes": [],
                "trigger_comment_id": comment_id,
                "created_at": datetime.utcnow().isoformat(),
            }
            await self.state_manager.update_state(task_id, initial_values)
            payload_to_send = initial_values

        return await self.worker_pool.add_task(
            task_id, 1, repo_name, issue_number, payload=payload_to_send
        )

    async def _execute_task(
        self, task_id: str, repo_name: str, issue_number: int, payload: dict = None
    ):
        """Huey ワーカーから呼び出される実行実体（ワーカープロセス内）"""
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

                    async for event in self.state_manager.astream(task_id, input_data):
                        # 各イベント後の最新状態を取得
                        current_state = await self.state_manager.get_state(task_id)
                        state_reported = current_state.values.get("reported_nodes", [])
                        if not isinstance(state_reported, list):
                            state_reported = []

                        # ローカルの即時記録とDBの状態をマージ
                        reported = set(state_reported).union(locally_reported)

                        for node_name, output in event.items():
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
                                        + get_footer(),
                                    )
                                    await self.state_manager.update_state(
                                        task_id,
                                        {
                                            "reported_nodes": list(
                                                reported.union({"intent_alignment"})
                                            )
                                        },
                                    )

                await asyncio.wait_for(run_workflow(), timeout=600)

                # 最終状態の報告
                final_state = await self.state_manager.get_state(task_id)
                final_status = final_state.values.get("status")
                state_reported = final_state.values.get("reported_nodes", [])
                if not isinstance(state_reported, list):
                    state_reported = []

                # ここでも最新の状態を確認して二重投稿を防止
                final_reported = set(state_reported)

                if (
                    final_status == "WaitingForClarification"
                    and "WaitingForClarification" not in final_reported
                ):
                    plan = final_state.values.get("plan", "No plan.")
                    await self.gh_client.post_comment(
                        repo_name,
                        issue_number,
                        f"### 🛠 実行計画（承認待ち）\n\n{plan}" + get_footer(),
                    )
                    await self.state_manager.update_state(
                        task_id,
                        {
                            "reported_nodes": list(
                                final_reported.union({"WaitingForClarification"})
                            )
                        },
                    )
                elif final_status == "Completed" and "Completed" not in final_reported:
                    summary = final_state.values.get("final_summary", "Done.")
                    await self.gh_client.post_comment(
                        repo_name,
                        issue_number,
                        f"### ✅ 完了報告\n\n{summary}" + get_footer(),
                    )
                    await self.state_manager.update_state(
                        task_id,
                        {"reported_nodes": list(final_reported.union({"Completed"}))},
                    )

            except asyncio.TimeoutError:
                logger.error(f"Task execution TIMEOUT: {task_id}")
                await self.gh_client.post_comment(
                    repo_name,
                    issue_number,
                    "### ⚠️ タイムアウトによる中断\n処理時間が制限（10分）を超えたため、安全のために実行を中断しました。特定の処理でループが発生したか、LLM の応答が停止した可能性があります。"
                    + get_footer(),
                )
                await self.state_manager.update_state(
                    task_id, {"status": "Failed"}, as_node="intent_alignment"
                )
            except TaskAbortedException as tae:
                logger.warning(f"Task {task_id} aborted by gate: {tae}")
                # ユーザーが意図的にクローズしたため、Failed ではなく Skipped またはそのまま終了
                await self.state_manager.update_state(
                    task_id, {"status": "Aborted"}, as_node="intent_alignment"
                )
            except Exception as e:
                logger.error(f"Task execution error: {e}", exc_info=True)
                # ルール 4 に基づき、エラー情報を簡潔に報告
                error_msg = str(e)
                await self.gh_client.post_comment(
                    repo_name,
                    issue_number,
                    f"### ❌ 実行エラーが発生しました\n\n原因: {error_msg}\n内部的な問題により処理を継続できないか、リソースが不足しています。"
                    + get_footer(),
                )
                await self.state_manager.update_state(
                    task_id, {"status": "Failed"}, as_node="intent_alignment"
                )
