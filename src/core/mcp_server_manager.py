import os
import sys
import pluggy
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import anyio
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from loguru import logger

from src.core.config import get_settings
from src.core.plugin_specs import MCPPluginSpec
from src.core.default_plugins import DirectoryDiscoveryPlugin


class MCPServerManager:
    """
    MCP サーバーのライフサイクルを管理する。
    プラグイン管理には Pluggy を使用し、拡張性と保守性を確保する。
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

        # Pluggy の初期化
        self.pm = pluggy.PluginManager("brownie")
        self.pm.add_hookspecs(MCPPluginSpec)
        
        # デフォルトのプラグイン発見器を登録
        self.pm.register(DirectoryDiscoveryPlugin(project_root))

    # --- Properties (Client accessors) ---
    @property
    def workspace_client(self) -> Optional[Client]: return self.clients.get("workspace")
    @property
    def knowledge_client(self) -> Optional[Client]: return self.clients.get("knowledge")
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
    def governance_client(self) -> Optional[Client]: return self.clients.get("governance")
    @property
    def worker_controller_client(self) -> Optional[Client]: return self.clients.get("worker_controller")
    @property
    def memory_client(self) -> Optional[Client]: return self.clients.get("memory")
    @property
    def sequential_thinking_client(self) -> Optional[Client]: return self.clients.get("sequential_thinking")
    @property
    def sqlite_client(self) -> Optional[Client]: return self.clients.get("sqlite")
    @property
    def postgres_client(self) -> Optional[Client]: return self.clients.get("postgres")

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

    # --- Core Infrastructure Servers ---
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

    async def start_repo_provision_server(self):
        return await self._start_server("repo_provision", sys.executable, ["-m", "src.mcp_server.repository_provision_server"])

    async def start_persistence_server(self):
        return await self._start_server("persistence", sys.executable, ["-m", "src.mcp_server.persistence_server"])

    async def start_history_server(self):
        return await self._start_server("history", sys.executable, ["-m", "src.mcp_server.history_server"])

    async def start_worker_server(self):
        return await self._start_server("worker", sys.executable, ["-m", "src.mcp_server.worker_server"])

    async def start_governance_server(self) -> Client:
        return await self._start_server("governance", sys.executable, ["-m", "src.mcp_server.governance_server"])

    async def start_worker_controller_server(self) -> Client:
        return await self._start_server("worker_controller", sys.executable, ["-m", "src.mcp_server.worker_controller_server"])

    # --- Official & Dynamic Plugins via Pluggy ---
    async def provision_servers(self, server_names: List[str]):
        """Pluggy Hook を用いてプラグインをオンデマンドで起動する"""
        if not self._exit_stack:
            logger.error("AsyncExitStack is not initialized.")
            return

        requested = set(server_names)
        current_plugins = {k for k in self.clients.keys() if k.startswith("plugin_")}
        
        # 不要なプラグインを停止
        for name in (current_plugins - {f"plugin_{s}" for s in requested}):
            logger.info(f"De-provisioning plugin context: {name}")
            del self.clients[name]

        # Pluggy Hook を呼び出して設定を取得し、起動する
        for name in requested:
            plugin_key = f"plugin_{name}"
            if plugin_key not in self.clients:
                # 全ての登録済みプラグインに対して Hook を呼び出す
                configs = self.pm.hook.get_server_config(name=name)
                # 最初に Non-None を返したものを採用
                config = next((c for c in configs if c), None)
                
                if config:
                    await self._start_server(
                        plugin_key,
                        config["command"],
                        config["args"],
                        {**(config.get("env") or {}), "BROWNIE_WORKSPACE_ROOT": self._repo_path}
                    )
                else:
                    logger.warning(f"Plugin configuration for '{name}' not found via Pluggy Hooks.")

    # --- Public APIs ---
    async def get_langchain_tools(self) -> List[Any]:
        """全アクティブサーバーのツールを抽出する"""
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
