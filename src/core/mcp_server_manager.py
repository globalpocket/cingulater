import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import anyio
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport

logger = logging.getLogger(__name__)

class MCPServerManager:
    """
    MCP サーバーのライフサイクルを管理する。
    コアサーバー（Workspace, Knowledge）と、タスクごとにJITロードされる解析用プラグインを管理する。
    AnyIO の TaskGroup を用いて、プロセスの確実なクリーンアップを保証する。
    """
    def __init__(self, project_root: str, config_path: Optional[str] = None):
        self.project_root = project_root
        self.config_path = config_path
        
        # コアサーバークライアント
        self.workspace_client: Optional[Client] = None
        self.knowledge_client: Optional[Client] = None
        self.planner_client: Optional[Client] = None
        self.writer_client: Optional[Client] = None
        self.resource_monitor_client: Optional[Client] = None
        self.github_sdk_client: Optional[Client] = None
        self.github_notifications_client: Optional[Client] = None
        self.repo_provision_client: Optional[Client] = None
        self.persistence_client: Optional[Client] = None
        self.history_client: Optional[Client] = None
        self.worker_client: Optional[Client] = None
        self.intent_interpreter_client: Optional[Client] = None
        self.governance_client: Optional[Client] = None
        
        # JIT ロードされるプラグインサーバークライアント
        self.plugin_clients: Dict[str, Client] = {}
        
        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        
        # 追加環境変数や実行時のコンテキスト
        self._repo_path: str = ""
        self._reference_path: str = ""
        self._memory_path: str = ""
        self._repo_name: str = ""
        
        # GitHub / Git 関連クライアント (既設)
        self.github_sdk_client = None
        self.github_notifications_client = None
        self.repo_provision_client = None
    

    async def start_workspace_server(self, repo_path: str, reference_path: str, user_id: int, group_id: int):
        """Workspace MCP Server を起動し、クライアントを返す（コア）"""
        self._repo_path = repo_path
        self._reference_path = reference_path
        
        logger.info(f"Starting Workspace MCP Server: workspace={repo_path}")
        env = {
            **os.environ, 
            "BROWNIE_WORKSPACE_ROOT": repo_path, 
            "BROWNIE_REFERENCE_ROOT": reference_path,
            "BROWNIE_CONFIG_PATH": self.config_path or "",
            "PYTHONPATH": "."
        }

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.workspace_server", repo_path, reference_path, str(user_id), str(group_id)],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.workspace_client = client
        logger.info("Workspace MCP Server connected successfully.")
        return client

    async def start_knowledge_server(self, repo_path: str, memory_path: str, repo_name: str):
        """Knowledge MCP Server を起動し、クライアントを返す（コア）"""
        self._memory_path = memory_path
        self._repo_name = repo_name
        
        logger.info(f"Starting Knowledge MCP Server for {repo_name}...")
        env = {
            **os.environ, 
            "BROWNIE_TARGET_REPO": repo_name, 
            "BROWNIE_REPO_PATH": repo_path, 
            "BROWNIE_MEMORY_PATH": memory_path,
            "BROWNIE_CONFIG_PATH": self.config_path or "",
            "PYTHONPATH": "."
        }

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.knowledge_server", repo_path, memory_path, repo_name],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.knowledge_client = client
        logger.info(f"Knowledge MCP Server connected successfully for {repo_name}")
        return client

    async def start_planner_server(self):
        """Code Planner MCP Server を起動し、クライアントを返す"""
        logger.info("Starting Code Planner MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.code_planner_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.planner_client = client
        logger.info("Code Planner MCP Server connected successfully.")
        return client

    async def start_writer_server(self):
        """Code Writer MCP Server を起動し、クライアントを返す"""
        logger.info("Starting Code Writer MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.code_writer_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.writer_client = client
        logger.info("Code Writer MCP Server connected successfully.")
        return client

    async def start_resource_monitor_server(self):
        """Resource Monitor MCP Server を起動し、クライアントを返す"""
        logger.info("Starting Resource Monitor MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.resource_monitor_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.resource_monitor_client = client
        logger.info("Resource Monitor MCP Server connected successfully.")
        return client

    async def start_github_sdk_server(self):
        """GitHub SDK MCP Server (@modelcontextprotocol/server-github) を起動"""
        logger.info("Starting GitHub SDK MCP Server...")
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        env = {
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": token or "",
            "GITHUB_TOKEN": token or ""
        }
        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.github_sdk_client = client
        logger.info("GitHub SDK MCP Server connected successfully.")
        return client

    async def start_github_notifications_server(self):
        """GitHub Notifications MCP Server (mcollina/github-notifications-mcp-server) を起動"""
        logger.info("Starting GitHub Notifications MCP Server...")
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        env = {
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": token or "",
            "GITHUB_TOKEN": token or ""
        }
        transport = StdioTransport(
            command="npx",
            args=["-y", "github-notifications-mcp-server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.github_notifications_client = client
        logger.info("GitHub Notifications MCP Server connected successfully.")
        return client

    async def start_repo_provision_server(self):
        """Repository Provision MCP Server (内製) を起動"""
        logger.info("Starting Repository Provision MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.repository_provision_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.repo_provision_client = client
        logger.info("Repository Provision MCP Server connected successfully.")
        return client

    async def start_persistence_server(self):
        """Persistence MCP Server (内製) を起動"""
        logger.info("Starting Persistence MCP Server...")
        db_path = ""
        if self.config_path:
            from src.utils.config_loader import get_config
            cfg = get_config(self.config_path)
            db_path = cfg.get("database", {}).get("db_path", "")

        env = {
            **os.environ,
            "BROWNIE_PERSISTENCE_DB": db_path,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.persistence_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.persistence_client = client
        logger.info("Persistence MCP Server connected successfully.")
        return client

    async def start_history_server(self):
        """History MCP Server (内製) を起動"""
        logger.info("Starting History MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.history_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.history_client = client
        logger.info("History MCP Server connected successfully.")
        return client

    async def start_worker_server(self):
        """Worker MCP Server (内製) を起動"""
        logger.info("Starting Worker MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.worker_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.worker_client = client
        logger.info("Worker MCP Server connected successfully.")
        return client

    async def start_intent_interpreter_server(self):
        """Intent Interpreter MCP Server (内製) を起動"""
        logger.info("Starting Intent Interpreter MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.intent_interpreter_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.intent_interpreter_client = client
        logger.info("Intent Interpreter MCP Server connected successfully.")
        return client

    async def start_governance_server(self):
        """Governance MCP Server (内製) を起動"""
        logger.info("Starting Governance MCP Server...")
        env = {
            **os.environ,
            "PYTHONPATH": "."
        }
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.mcp_server.governance_server"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self.governance_client = client
        logger.info("Governance MCP Server connected successfully.")
        return client

    async def provision_servers(self, server_names: List[str]):
        """
        要求されたJITロードMCPサーバー群をオンデマンドで起動する。
        既に起動中の不要なプラグインは停止（リソース解放）し、必要なものだけを起動。
        """
        if not self._exit_stack:
            logger.error("AsyncExitStack is not initialized. Call within `async with manager:` block.")
            return

        logger.info(f"Provisioning JIT MCP Servers: {server_names}")
        
        current_plugins = set(self.plugin_clients.keys())
        requested_plugins = set(server_names)
        
        to_remove = current_plugins - requested_plugins
        to_add = requested_plugins - current_plugins
        
        # 削除対象は今回は簡易的に辞書から外すだけ（PythonのGCとプロセス管理に委ねる、
        # または完全に機能させるなら AsyncExitStack を個別プラグイン単位で管理する必要がある）
        for name in to_remove:
            logger.info(f"De-provisioning JIT Server: {name}")
            del self.plugin_clients[name]
            
        for name in to_add:
            try:
                await self._start_plugin_server(name)
            except Exception as e:
                logger.error(f"Failed to start plugin server '{name}': {e}")
                
    async def _start_plugin_server(self, name: str):
        logger.info(f"Starting Plugin MCP Server: {name}")
        env = {
            **os.environ, 
            "BROWNIE_WORKSPACE_ROOT": self._repo_path, 
            "BROWNIE_CONFIG_PATH": self.config_path or "",
            "PYTHONPATH": "."
        }
        
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", f"src.mcp_server.plugins.{name}"],
            env=env,
            cwd=self.project_root,
            keep_alive=False
        )
        
        client = Client(transport)
        # 本来は個別の ExitStack で管理し、停止時に aclose するのが望ましいが
        # 今回はライフサイクルの範囲内で enter_async_context で管理
        await self._exit_stack.enter_async_context(client)
        self.plugin_clients[name] = client
        logger.info(f"Plugin MCP Server '{name}' connected successfully.")


    async def get_langchain_tools(self) -> List[Any]:
        """
        全アクティブサーバーから提供されるツールを LangChain 形式に変換して取得する。
        """
        from langchain_mcp_adapters.tools import load_mcp_tools
        
        all_tools = []
        clients = []
        if self.workspace_client:
            clients.append(self.workspace_client)
        if self.knowledge_client:
            clients.append(self.knowledge_client)
        if self.planner_client:
            clients.append(self.planner_client)
        if self.writer_client:
            clients.append(self.writer_client)
        if self.resource_monitor_client:
            clients.append(self.resource_monitor_client)
        if self.github_sdk_client:
            clients.append(self.github_sdk_client)
        if self.github_notifications_client:
            clients.append(self.github_notifications_client)
        if self.repo_provision_client:
            clients.append(self.repo_provision_client)
        if self.persistence_client:
            clients.append(self.persistence_client)
        if self.history_client:
            clients.append(self.history_client)
        if self.worker_client:
            clients.append(self.worker_client)
        if self.intent_interpreter_client:
            clients.append(self.intent_interpreter_client)
        if self.governance_client:
            clients.append(self.governance_client)
            
        clients.extend(self.plugin_clients.values())
        
        for client in clients:
            if client and client.session:
                tools = await load_mcp_tools(client.session)
                all_tools.extend(tools)
        
        logger.info(f"Loaded {len(all_tools)} MCP tools via LangChain Adapter.")
        return all_tools

    async def stop_all(self):
        """全ての MCP サーバーを停止する"""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = AsyncExitStack()
            self.plugin_clients.clear()

    async def __aenter__(self):
        self._exit_stack = AsyncExitStack()
        self._task_group = await self._exit_stack.enter_async_context(anyio.create_task_group())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._exit_stack:
            await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
