import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import anyio
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from loguru import logger

from src.core.config import get_settings


class MCPServerManager:
    """
    MCP サーバーのライフサイクルを管理する。
    コアサーバー（Workspace, Knowledge）と、タスクごとにJITロードされる解析用プラグインを管理する。
    AsyncExitStack を用いて、プロセスの確実なクリーンアップを保証する。
    """
    def __init__(self, project_root: str, config_path: Optional[str] = None):
        self.project_root = project_root
        self.settings = get_settings(config_path)
        
        # 全てのクライアントを辞書で一元管理
        self.clients: Dict[str, Client] = {}
        
        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        
        # 実行時のコンテキスト
        self._repo_path: str = ""
        self._reference_path: str = ""
        self._memory_path: str = ""
        self._repo_name: str = ""

    @property
    def workspace_client(self) -> Optional[Client]: return self.clients.get("workspace")
    @property
    def knowledge_client(self) -> Optional[Client]: return self.clients.get("knowledge")
    @property
    def planner_client(self) -> Optional[Client]: return self.clients.get("planner")
    @property
    def writer_client(self) -> Optional[Client]: return self.clients.get("writer")
    @property
    def resource_monitor_client(self) -> Optional[Client]: return self.clients.get("resource_monitor")
    @property
    def github_sdk_client(self) -> Optional[Client]: return self.clients.get("github_sdk")
    @property
    def github_notifications_client(self) -> Optional[Client]: return self.clients.get("github_notifications")
    @property
    def repo_provision_client(self) -> Optional[Client]: return self.clients.get("repo_provision")
    @property
    def persistence_client(self) -> Optional[Client]: return self.clients.get("persistence")
    @property
    def history_client(self) -> Optional[Client]: return self.clients.get("history")
    @property
    def worker_client(self) -> Optional[Client]: return self.clients.get("worker")
    @property
    def intent_interpreter_client(self) -> Optional[Client]: return self.clients.get("intent_interpreter")
    @property
    def governance_client(self) -> Optional[Client]: return self.clients.get("governance")
    @property
    def worker_controller_client(self) -> Optional[Client]: return self.clients.get("worker_controller")
    @property
    def task_reasoning_client(self) -> Optional[Client]: return self.clients.get("task_reasoning")

    async def _start_server(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> Client:
        """共通の起動エンジン"""
        logger.info(f"Starting {name} MCP Server: {command} {' '.join(args)}")
        
        transport = StdioTransport(
            command=command,
            args=args,
            env={**os.environ, **(env or {}), "PYTHONPATH": "."},
            cwd=self.project_root,
            keep_alive=False
        )
        
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.clients[name] = client
        logger.info(f"{name} MCP Server connected successfully.")
        return client

    async def start_workspace_server(self, repo_path: str, reference_path: str, user_id: int, group_id: int):
        self._repo_path = repo_path
        self._reference_path = reference_path
        return await self._start_server(
            "workspace",
            sys.executable,
            ["-m", "src.mcp_server.workspace_server", repo_path, reference_path, str(user_id), str(group_id)],
            {"BROWNIE_WORKSPACE_ROOT": repo_path, "BROWNIE_REFERENCE_ROOT": reference_path}
        )

    async def start_knowledge_server(self, repo_path: str, memory_path: str, repo_name: str):
        self._memory_path = memory_path
        self._repo_name = repo_name
        return await self._start_server(
            "knowledge",
            sys.executable,
            ["-m", "src.mcp_server.knowledge_server", repo_path, memory_path, repo_name],
            {"BROWNIE_TARGET_REPO": repo_name, "BROWNIE_REPO_PATH": repo_path, "BROWNIE_MEMORY_PATH": memory_path}
        )

    async def start_planner_server(self):
        return await self._start_server("planner", sys.executable, ["-m", "src.mcp_server.code_planner_server"])

    async def start_writer_server(self):
        return await self._start_server("writer", sys.executable, ["-m", "src.mcp_server.code_writer_server"])

    async def start_resource_monitor_server(self):
        return await self._start_server("resource_monitor", sys.executable, ["-m", "src.mcp_server.resource_monitor_server"])

    async def start_github_sdk_server(self):
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        return await self._start_server(
            "github_sdk",
            "npx",
            ["-y", "@modelcontextprotocol/server-github"],
            {"GITHUB_PERSONAL_ACCESS_TOKEN": token or "", "GITHUB_TOKEN": token or ""}
        )

    async def start_github_notifications_server(self):
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        return await self._start_server(
            "github_notifications",
            "npx",
            ["-y", "github-notifications-mcp-server"],
            {"GITHUB_PERSONAL_ACCESS_TOKEN": token or "", "GITHUB_TOKEN": token or ""}
        )

    async def start_repo_provision_server(self):
        return await self._start_server("repo_provision", sys.executable, ["-m", "src.mcp_server.repository_provision_server"])

    async def start_persistence_server(self):
        return await self._start_server(
            "persistence",
            sys.executable,
            ["-m", "src.mcp_server.persistence_server"]
        )

    async def start_history_server(self):
        return await self._start_server("history", sys.executable, ["-m", "src.mcp_server.history_server"])

    async def start_worker_server(self):
        return await self._start_server("worker", sys.executable, ["-m", "src.mcp_server.worker_server"])

    async def start_intent_interpreter_server(self) -> Client:
        return await self._start_server("intent_interpreter", sys.executable, ["-m", "src.mcp_server.intent_interpreter_server"])

    async def start_governance_server(self) -> Client:
        return await self._start_server("governance", sys.executable, ["-m", "src.mcp_server.governance_server"])

    async def start_worker_controller_server(self) -> Client:
        return await self._start_server("worker_controller", sys.executable, ["-m", "src.mcp_server.worker_controller_server"])

    async def start_task_reasoning_server(self) -> Client:
        return await self._start_server("task_reasoning", sys.executable, ["-m", "src.mcp_server.task_reasoning_server"])

    async def start_fetch_server(self):
        """公式の fetch サーバーを起動し、高品質な Markdown 取得を実現する"""
        return await self._start_server(
            "fetch",
            "npx",
            ["-y", "@modelcontextprotocol/server-fetch"]
        )

    async def provision_servers(self, server_names: List[str]):
        """要求されたJITロードMCPサーバー群をオンデマンドで起動する"""
        if not self._exit_stack:
            logger.error("AsyncExitStack is not initialized.")
            return

        requested = set(server_names)
        current_plugins = {k for k in self.clients.keys() if k.startswith("plugin_")}
        
        # 不要なプラグインを停止（実際には ExitStack 単位での管理が必要だが、一旦辞書から削除）
        for name in (current_plugins - {f"plugin_{s}" for s in requested}):
            logger.info(f"De-provisioning JIT Server: {name}")
            del self.clients[name]

        # 新規プラグインを起動
        for name in requested:
            plugin_key = f"plugin_{name}"
            if plugin_key not in self.clients:
                await self._start_server(
                    plugin_key,
                    sys.executable,
                    ["-m", f"src.mcp_server.plugins.{name}"],
                    {"BROWNIE_WORKSPACE_ROOT": self._repo_path}
                )




    async def get_langchain_tools(self) -> List[Any]:
        """全アクティブサーバーから提供されるツールを LangChain 形式に変換して取得する"""
        from langchain_mcp_adapters.tools import load_mcp_tools
        
        all_tools = []
        for name, client in self.clients.items():
            if client and client.session:
                try:
                    tools = await load_mcp_tools(client.session)
                    all_tools.extend(tools)
                    logger.debug(f"Loaded {len(tools)} tools from {name}")
                except Exception as e:
                    logger.error(f"Failed to load tools from {name}: {e}")
        
        logger.info(f"Total {len(all_tools)} MCP tools loaded.")
        return all_tools

    async def stop_all(self):
        """全ての MCP サーバーを停止する"""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = AsyncExitStack()
            self.clients.clear()

    async def __aenter__(self):
        self._exit_stack = AsyncExitStack()
        self._task_group = await self._exit_stack.enter_async_context(anyio.create_task_group())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._exit_stack:
            await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
