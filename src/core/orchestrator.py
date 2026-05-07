# src/core/orchestrator.py
import os
import time
import yaml
import json
import asyncio
import logging
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from core.schema import InternalAgentRequest, InternalMessage, InternalTool
from core.events import (
    AgentEvent,
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)
from core.llm_client import OpenAILLMClient
from core.interceptors import (
    InterceptorPipeline,
    SystemPromptInterceptor,
    ToolHallucinationInterceptor,
    ReflectionInterceptor,
    ModelConfigurationInterceptor,
    ErrorHandlingInterceptor,
    LoggingInterceptor,
    ContextLimitInterceptor,
    WorkflowInterceptorPipeline,
    WorkflowLoadInterceptor,
    ToolFetchInterceptor,
    WorkflowExecutionInterceptor
)


# ==========================================
# 1. Config & Settings
# ==========================================
class AgentSettings(BaseModel):
    max_retries: int = Field(default=3)
    single_task_mode: bool = Field(default=False)

class LLMSettings(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    interlocutor_endpoint: str = Field(default="http://localhost:8080/v1")
    timeout_sec: int = Field(default=120)
    launcher_client: Optional[str] = Field(default="mlx-launcher")
    launcher_tool: Optional[str] = Field(default="launch_llm_server")

class WorkspaceSettings(BaseModel):
    sandbox_user: str = Field(default="cingulater_sandbox")
    base_path: str = Field(default="./workspace")

class Settings(BaseSettings):
    agent: AgentSettings = Field(default_factory=AgentSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

    @classmethod
    def load(cls, config_path: str) -> "Settings":
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
            return cls(**yaml_data)
        except Exception as e:
            logger.error(f"Failed to load config from {config_path}: {e}")
            return cls()

def get_settings(config_path: str = "config.yaml") -> Settings:
    return Settings.load(config_path)


# ==========================================
# 2. Gateway Client (Task-based Lifecycle Management)
# ==========================================
class GatewayClient:
    def __init__(self, command: str, args: Optional[List[str]] = None):
        self.command = command
        self.args = args or []
        self.session: Optional[ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._init_event = asyncio.Event()

    async def start(self):
        """Starts the MCP server in a background task to maintain the context."""
        self._stop_event.clear()
        self._init_event.clear()
        self._task = asyncio.create_task(self._run())
        
        # Wait for initialization or error
        await self._init_event.wait()
        if not self.session:
            logger.error(f"Failed to initialize MCP session for {self.command}. Continuing without it.")

    async def _run(self):
        try:
            server_params = StdioServerParameters(
                command=self.command, 
                args=self.args, 
                env=os.environ.copy()
            )
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    self.session = session
                    await session.initialize()
                    logger.info(f"✅ Successfully connected to MCP via {self.command}.")
                    self._init_event.set()
                    
                    # Wait until stop() is called
                    await self._stop_event.wait()
        except Exception as e:
            logger.error(f"❌ MCP session error ({self.command}): {e}")
            # Ensure initialization wait is released even on error
            self._init_event.set()
        finally:
            self.session = None

    async def stop(self):
        """Signals the background task to exit gracefully."""
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def fetch_tools(self) -> List[Dict[str, Any]]:
        if not self.session:
            return []
        try:
            tools_result = await self.session.list_tools()
            return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools_result.tools]
        except Exception as e:
            logger.error(f"Failed to fetch tools: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if not self.session:
            raise ValueError(f"Gateway ({self.command}) is not connected.")
        result = await self.session.call_tool(tool_name, arguments)
        output = ""
        for content in result.content:
            if isinstance(content, types.TextContent):
                output += content.text + "\n"
        return output.strip()


# ==========================================
# 3. Core Orchestrator
# ==========================================
class Orchestrator:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.system_prompt_path = self.project_root / ".cingulater" / "system_prompt.md"
        self.mcp_config_path = self.project_root / "mcp_config.json"
        
        self.system_prompt = self._load_system_prompt()
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.llm_client = OpenAILLMClient()
        
        self.llm_pipeline = InterceptorPipeline([
            LoggingInterceptor(),
            ContextLimitInterceptor(max_messages=2000),
            SystemPromptInterceptor(),
            ModelConfigurationInterceptor(),
            ToolHallucinationInterceptor(),
            ReflectionInterceptor(), # Restored: Evaluates content to trigger finish tools
            ErrorHandlingInterceptor()
        ])

        self.workflow_pipeline = WorkflowInterceptorPipeline([
            WorkflowLoadInterceptor(),
            ToolFetchInterceptor(), # Restored: Fetches tools for reflection/reranking
            WorkflowExecutionInterceptor()
        ])

        self.mcp_clients: Dict[str, GatewayClient] = {}
        
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                    for name, config in servers.items():
                        if name == "mcp-routing-gateway":
                            continue
                            
                        cmd = config.get("command")
                        args = config.get("args", [])
                        
                        if cmd:
                            self.mcp_clients[name] = GatewayClient(command=cmd, args=args)
            except Exception as e:
                logger.error(f"Failed to load mcp_config.json: {e}")

    async def start(self):
        # Start all MCP clients
        for name, client in self.mcp_clients.items():
            logger.info(f"Starting MCP Client: {name}")
            await client.start()
            
        # Auto-Launch LLM Server
        await self._launch_llm_server()
            
        logger.info("✅ Orchestrator: Hybrid-Workflow engine ready.")

    async def _launch_llm_server(self):
        try:
            launcher_client_name = self.settings.llm.launcher_client
            launcher_tool_name = self.settings.llm.launcher_tool
            
            if launcher_client_name and launcher_tool_name:
                launcher_client = self.mcp_clients.get(launcher_client_name)
                if launcher_client and launcher_client.session:
                    for key in ["interlocutor"]:
                        model = self.settings.llm.models.get(key)
                        if model:
                            port = urlparse(getattr(self.settings.llm, f"{key}_endpoint")).port or 8080
                            logger.info(f"Launching/Restarting LLM Server for {key} model: {model} on port {port}")
                            await launcher_client.call_tool(launcher_tool_name, {"model_name": model, "port": port})
                else:
                    logger.debug(f"Launcher client '{launcher_client_name}' is missing or offline. Skipping auto-launch.")
        except Exception as e:
            logger.error(f"Auto-launch failed: {e}")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "You are CINGULATER."

    async def _extract_intent(self, text: str) -> str:
        """Restored: Extracted intent for Reranker evaluation."""
        prompt = (
            "Translate the following text to English, extract the core user intent, "
            "and summarize it in a short phrase (e.g., 'Asking a clarifying question', 'Completed the task'). "
            "Output ONLY the summary phrase.\n\n"
            f"Text: {text}"
        )
        endpoint = self.settings.llm.interlocutor_endpoint
        model_name = self.settings.llm.models.get("interlocutor", "default")
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10240,
            "temperature": 0.0,
            "stream": False
        }
        
        try:
            resp = await self.http_client.post(f"{endpoint}/chat/completions", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if content:
                    logger.debug(f"[Intent Extraction] Original -> Intent: {content}")
                    return content
            logger.warning(f"Intent extraction failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.exception("Intent extraction error:")
            
        return "Unknown intent"

    async def process_workflow(self, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        actor = "interlocutor"
        workflow_steps = [{"type": "llm_chat", "model_key": "interlocutor"}]
        logger.info(f"Selected Actor: {actor}")
        
        async for event in self.workflow_pipeline.process(actor, request, self, self._raw_run_workflow, workflow_steps=workflow_steps):
            yield event

    async def _raw_run_workflow(self, actor: str, request: InternalAgentRequest, **kwargs) -> AsyncGenerator[AgentEvent, None]:
        steps = kwargs.get("workflow_steps", [])
        final_reason = "stop"

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                yield ErrorEvent(message=f"Step {i+1} is missing required 'model_key'.")
                return

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            
            async for event in self._call_llm(model_key, endpoint, request):
                if isinstance(event, WorkflowFinishEvent):
                    final_reason = event.finish_reason
                else:
                    yield event
        
        yield WorkflowFinishEvent(finish_reason=final_reason)

    async def _call_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        processed_request = await self.llm_pipeline.pre_process(
            request.model_copy(deep=True), self, model_key=model_key, endpoint=endpoint
        )
        raw_stream = self._raw_stream_llm(model_key, endpoint, processed_request)
        async for event in self.llm_pipeline.post_process_stream(
            raw_stream, processed_request, self, model_key=model_key, endpoint=endpoint
        ):
            yield event

    async def _raw_stream_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        json_payload = request.model_dump(exclude_none=True)
        final_finish_reason = "stop"
        
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                async for chunk in self.llm_client.stream_chat(endpoint, json_payload, self.settings.llm.timeout_sec):
                    if chunk.content:
                        yield TextDeltaEvent(content=chunk.content)
                    
                    if chunk.tool_calls:
                        for tc in chunk.tool_calls:
                            func_name = tc.name
                            args_str = tc.arguments or ""
                            tc_id = tc.id or f"call_{tc.index}"
                            
                            if func_name:
                                yield ToolCallStartEvent(index=tc.index, id=tc_id, tool_name=func_name)
                            if args_str:
                                yield ToolCallDeltaEvent(index=tc.index, arguments=args_str)
                                
                    if chunk.finish_reason:
                        final_finish_reason = chunk.finish_reason

                yield WorkflowFinishEvent(finish_reason=final_finish_reason)
                break  # Success, exit retry loop
            
            except Exception as e:
                error_msg = str(e).lower()
                # 接続エラーやタイムアウトの場合にリトライを試みる
                if "connect" in error_msg or "timeout" in error_msg:
                    if attempt < max_retries:
                        logger.warning(f"LLM connection error: {e}. Attempting self-healing (relaunching server)...")
                        await self._launch_llm_server()
                        await asyncio.sleep(20)  # サーバー起動待ち (変更: 2秒から20秒へ延長)
                        continue
                # リトライ上限、または対象外のエラーの場合は再送出
                raise e

    async def shutdown(self):
        for client in self.mcp_clients.values():
            await client.stop()
        await self.http_client.aclose()