from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from src.core.mcp_server_manager import MCPServerManager
from src.core.sandbox_manager import SandboxManager, WorkspaceContext


class InfrastructureBridge:
    """
    システムのインフラ操作 (MCPツール) を一元管理するブリッジ。
    各ノードが直接 call_tool を叩くのを避け、Semantic なメソッド経由で操作する。
    """

    def __init__(self, mcp_manager: MCPServerManager, token: str):
        self.mcp_manager = mcp_manager
        self._token = token

    async def close(self):
        """ブリッジのリソースをクリーンアップします。"""
        # 現在は MCPServerManager が全サーバーを停止するため、
        # ブリッジ側で個別に閉じる必要があるセッション等があればここに実装。
        pass

    async def enqueue_repair_task(
        self, task_id: str, repo_name: str, issue_number: int, error_context: str
    ):
        """Worker Controller MCP を通じて修復タスクをキューイングする"""
        client = self.mcp_manager.worker_controller_client
        if not client:
            logger.error("Worker Controller MCP is not available.")
            return False
        try:
            await client.call_tool(
                "enqueue_task",
                task_type="repair",
                task_id=task_id,
                repo_name=repo_name,
                issue_number=issue_number,
                payload={"error_context": error_context},
            )
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue repair task: {e}")
            return False

    async def post_comment(self, repo_name: str, issue_number: int, body: str):
        """GitHub Platform MCP を通じてコメントを投稿する"""
        client = self.mcp_manager.repo_provision_client
        if not client:
            return
        await client.call_tool("post_comment", {"repo_full_name": repo_name, "issue_number": issue_number, "body": body, "token": self._token})

    async def get_issue(self, repo_name: str, issue_number: int) -> Dict[str, Any]:
        """GitHub Platform MCP を通じて Issue 情報を取得する"""
        client = self.mcp_manager.repo_provision_client
        if not client:
            return {}
        return await client.call_tool("get_issue", {"repo_full_name": repo_name, "issue_number": issue_number, "token": self._token})

    async def get_mentions_to_process(self) -> List[Dict[str, Any]]:
        """GitHub Platform MCP を通じてメンション（通知）を取得する"""
        client = self.mcp_manager.repo_provision_client
        if not client:
            return []
        return await client.call_tool("get_mentions", {"token": self._token})

    async def ensure_repo_cloned(
        self, repo_name: str, repo_path: str, branch_name: Optional[str] = None
    ):
        """Repository Provision MCP を通じてリポジトリをクローン/最新化する"""
        client = self.mcp_manager.repo_provision_client
        if not client:
            logger.error("Repo Provision MCP is not available.")
            return
        try:
            await client.call_tool(
                "provision_repository",
                repo_name=repo_name,
                repo_path=repo_path,
                token=self._token,
                branch_name=branch_name,
            )
        except Exception as e:
            logger.error(f"Failed to provision repository via MCP: {e}")
            raise

    async def execute_reasoning_loop(
        self,
        instruction: str,
        task_id: str,
        repo_name: str,
        issue_number: int,
        model_name: str,
        endpoint: str,
    ) -> Dict[str, Any]:
        """Task Reasoning MCP を介して推論ループを実行する"""
        client = self.mcp_manager.task_reasoning_client
        if not client:
            logger.error("Task Reasoning MCP Client is not available.")
            return {"status": "failed", "error": "MCP not ready"}

        try:
            return await client.call_tool(
                "execute_reasoning_loop",
                instruction=instruction,
                task_id=task_id,
                repo_name=repo_name,
                issue_number=issue_number,
                model_name=model_name,
                endpoint=endpoint,
            )
        except Exception as e:
            logger.error(f"Reasoning loop delegation failed: {e}")
            return {"status": "failed", "error": str(e)}


class AgentDeps:
    def __init__(
        self,
        config: Dict[str, Any],
        sandbox: SandboxManager,
        infra_bridge: "InfrastructureBridge",
        mcp_manager: MCPServerManager,
        workspace_context: Optional[WorkspaceContext] = None,
    ):
        self.config = config
        self.sandbox = sandbox
        self.infra_bridge = infra_bridge
        self.mcp_manager = mcp_manager
        self.workspace_context = workspace_context
        self.current_task_id: Optional[str] = None
        self.current_repo_name: Optional[str] = None
        self.current_issue_number: Optional[int] = None


class CoderAgent:
    """
    推論ループを TaskReasoning MCP サーバーに委任するブリッジ。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        sandbox: SandboxManager,
        infra_bridge: "InfrastructureBridge",
        mcp_manager: MCPServerManager,
        workspace_context: Optional[WorkspaceContext] = None,
    ):
        self.deps = AgentDeps(
            config, sandbox, infra_bridge, mcp_manager, workspace_context
        )
        self.config = config

        # WorkflowManager の初期化 (Circular dependency を避けるためローカルインポート)
        from src.core.workflow_manager import WorkflowLoader

        project_root = Path(mcp_manager.project_root)
        workspace_root = (
            Path(workspace_context.repo_path) if workspace_context else None
        )
        self.workflow_loader = WorkflowLoader(
            project_root, mcp_manager=mcp_manager, workspace_root=workspace_root
        )
        # 動的ツールのロード (MCPツールとの重複チェックは同期のため実行)
        self.workflow_loader.load_all(config=config)

    async def run(
        self, task_id: str, repo_name: str, issue_number: int, **kwargs
    ) -> Union[bool, str]:
        """推論ループを委任実行"""
        instruction = kwargs.get(
            "task_description", f"Issue #{issue_number} を解決してください。"
        )

        planner_model = self.config["llm"]["models"]["planner"]
        planner_endpoint = self.config["llm"]["planner_endpoint"]

        # InfrastructureBridge を通じて実行
        logger.info(f"[{task_id}] Delegating reasoning loop via bridge...")
        result = await self.deps.infra_bridge.execute_reasoning_loop(
            instruction=instruction,
            task_id=task_id,
            repo_name=repo_name,
            issue_number=issue_number,
            model_name=planner_model,
            endpoint=planner_endpoint,
        )

        status = result.get("status")
        if status == "finished":
            return True
        elif status == "waiting_for_clarification":
            return "WAITING"
        elif "blueprint" in result:
            return "BLUEPRINT_GENERATED"

        return False
