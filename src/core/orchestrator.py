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
from contextlib import AsyncExitStack

import httpx
from loguru import logger
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel
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
    ReflectionInterceptor
)


# ==========================================
# 1. Config & Settings
# ==========================================
class AgentSettings(BaseModel):
    max_retries: int = Field(default=3)

class LLMSettings(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    interlocutor_endpoint: str = Field(default="http://localhost:8080/v1")
    coder_endpoint: str = Field(default="http://localhost:8081/v1")
    timeout_sec: int = Field(default=120)
    launcher_client: Optional[str] = Field(default="mlx-launcher")
    launcher_tool: Optional[str] = Field(default="launch_llm_server")

class WorkspaceSettings(BaseModel):
    sandbox_user: str = Field(default="brownie_sandbox")
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
# 2. Router
# ==========================================
class Router:
    def __init__(self, settings: Settings, workflows_dir: Path, orchestrator: "Orchestrator"):
        self.settings = settings
        self.workflows_dir = workflows_dir
        self.orchestrator = orchestrator
        logger.info("Intent Reranker Router initialized.")

    async def route(self, messages: List[InternalMessage]) -> str:
        actors = []
        documents = []
        
        for p in self.workflows_dir.glob("*.yaml"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    wf_data = yaml.safe_load(f) or {}
                    name = wf_data.get("name", p.stem)
                    desc = wf_data.get("description", f"Expert named {name}")
                    actors.append(name)
                    documents.append(desc)
            except Exception as e:
                logger.error(f"Failed to load workflow {p}: {e}")
                
        if not actors:
            return "interlocutor"
            
        recent_msgs = messages[-5:]
        history_text = ""
        for m in recent_msgs:
            role = m.role
            content = m.content or ""
            if content and len(content) > 500:
                content = content[:500] + " ...[truncated]"
            history_text += f"[{role}]: {content}\n"

        intent = await self.orchestrator._extract_intent(history_text)
        
        try:
            reranker_client = self.orchestrator.mcp_clients.get("mcp-reranker")
            if reranker_client:
                result_str = await reranker_client.call_tool(
                    "rerank_documents", 
                    {"query": intent, "documents": documents}
                )
                results = json.loads(result_str)
                
                if results:
                    best_doc = results[0]["document"]
                    best_idx = documents.index(best_doc)
                    selected_actor = actors[best_idx]
                    
                    logger.info(f"Router selected '{selected_actor}' with score {results[0]['score']:.4f} (Intent: {intent})")
                    return selected_actor
                else:
                    logger.warning("mcp-reranker returned empty results. Defaulting to interlocutor.")
                    return "interlocutor"
            else:
                logger.warning("mcp-reranker client not connected. Defaulting to interlocutor.")
                return "interlocutor"
            
        except Exception as e:
            logger.error(f"Router Reranker Error: {e}. Defaulting to interlocutor.")
            return "interlocutor"


# ==========================================
# 3. Gateway Client
# ==========================================
class GatewayClient:
    def __init__(self, command: str = "mcp-routing-gateway", args: Optional[List[str]] = None):
        self.command = command
        self.args = args or []
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()

    async def start(self):
        try:
            server_params = StdioServerParameters(
                command=self.command, 
                args=self.args, 
                env=os.environ.copy()
            )
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            read_stream, write_stream = stdio_transport
            self.session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self.session.initialize()
            logger.info(f"✅ Successfully connected to MCP Routing Gateway via {self.command}.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Gateway: {e}")
            raise

    async def stop(self):
        await self._exit_stack.aclose()
        self.session = None

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
            raise ValueError("Gateway is not connected.")
        result = await self.session.call_tool(tool_name, arguments)
        output = ""
        for content in result.content:
            if isinstance(content, types.TextContent):
                output += content.text + "\n"
        return output.strip()


# ==========================================
# 4. Core Orchestrator
# ==========================================
class MCPVirtualTool(Tool):
    def __init__(self, mcp_tool_def, mcp_client: GatewayClient, loop):
        self.name = mcp_tool_def["name"]
        self.description = mcp_tool_def["description"]
        props = mcp_tool_def.get("inputSchema", {}).get("properties", {})
        self.inputs = {k: {"type": v.get("type", "string"), "description": v.get("description", "")} for k, v in props.items()}
        self.output_type = "string"
        self.mcp_client = mcp_client
        self._loop = loop
        self.is_initialized = True
        self.skip_forward_signature_validation = True
        super().__init__()

    def forward(self, **kwargs):
        future = asyncio.run_coroutine_threadsafe(self.mcp_client.call_tool(self.name, kwargs), self._loop)
        return future.result()


class Orchestrator:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.workflows_dir = self.project_root / "workflows"
        self.system_prompt_path = self.project_root / ".brwn" / "system_prompt.md"
        self.mcp_config_path = self.project_root / "brownie_core_mcp_config.json"
        
        self.system_prompt = self._load_system_prompt()
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.router = Router(settings=self.settings, workflows_dir=self.workflows_dir, orchestrator=self)
        self.llm_client = OpenAILLMClient()
        
        # インターセプターパイプラインの登録
        self.pipeline = InterceptorPipeline([
            SystemPromptInterceptor(),
            ToolHallucinationInterceptor(),
            ReflectionInterceptor()
        ])
        
        self.mcp_clients: Dict[str, GatewayClient] = {}
        
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                    for name, config in servers.items():
                        cmd = config.get("command")
                        args = config.get("args", [])
                        
                        if name == "mcp-routing-gateway":
                            cmd = os.getenv("BROWNIE_GATEWAY_CMD", cmd)
                            
                        if cmd:
                            self.mcp_clients[name] = GatewayClient(command=cmd, args=args)
            except Exception as e:
                logger.error(f"Failed to load brownie_core_mcp_config.json: {e}")

        if not self.mcp_clients:
            cmd = os.getenv("BROWNIE_GATEWAY_CMD", "mcp-routing-gateway")
            self.mcp_clients["mcp-routing-gateway"] = GatewayClient(command=cmd, args=[])

    async def start(self):
        for name, client in self.mcp_clients.items():
            logger.info(f"Starting MCP Client: {name}")
            await client.start()
            
        try:
            launcher_client_name = self.settings.llm.launcher_client
            launcher_tool_name = self.settings.llm.launcher_tool
            
            if launcher_client_name and launcher_tool_name:
                launcher_client = self.mcp_clients.get(launcher_client_name)
                if launcher_client:
                    for key in ["interlocutor", "coder"]:
                        model = self.settings.llm.models.get(key)
                        if model:
                            port = urlparse(getattr(self.settings.llm, f"{key}_endpoint")).port or 8080
                            await launcher_client.call_tool(launcher_tool_name, {"model_name": model, "port": port})
                else:
                    logger.debug(f"Launcher client '{launcher_client_name}' not found. Skipping auto-launch.")
        except Exception as e:
            logger.error(f"Auto-launch failed: {e}")
        logger.info("✅ Orchestrator: Hybrid-Workflow engine ready.")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "You are BROWNIE."

    async def _extract_intent(self, text: str) -> str:
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
            "max_tokens": 1024,
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
            logger.error(f"Intent extraction error: {e}")
            
        return "Unknown intent"

    async def process_workflow(self, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        actor = await self.router.route(request.messages)
        logger.info(f"Selected Actor: {actor}")
        
        async for event in self._run_workflow(actor, request):
            yield event

    async def _run_workflow(self, actor: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        workflow_path = self.workflows_dir / f"{actor}.yaml"
        
        if not workflow_path.exists():
            yield ErrorEvent(message="Workflow not found")
            return

        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                steps = yaml.safe_load(f).get("steps", [])
        except Exception as e:
            yield ErrorEvent(message=f"Workflow parse error: {e}")
            return

        mcp_tools = []
        loop = asyncio.get_running_loop()
        for name, client in self.mcp_clients.items():
            try:
                tools = await client.fetch_tools()
                for t in tools:
                    mcp_tools.append(MCPVirtualTool(t, client, loop))
            except Exception as e:
                logger.warning(f"Failed to fetch tools from {name}: {e}")

        final_reason = "stop"

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                yield ErrorEvent(message=f"Step {i+1} is missing required 'model_key'.")
                return

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            
            if step.get("type") == "llm_chat":
                async for event in self._call_llm(model_key, endpoint, request):
                    if isinstance(event, WorkflowFinishEvent):
                        final_reason = event.finish_reason
                    else:
                        yield event
            
            elif step.get("type") == "agent_task":
                yield TextDeltaEvent(content=f"\n[Step {i+1} Start]\n")
                agent_model = OpenAIServerModel(model_id=self.settings.llm.models.get(model_key), api_base=endpoint, api_key="none")
                max_steps = self.settings.agent.max_retries
                agent = ToolCallingAgent(tools=mcp_tools, model=agent_model, max_steps=max_steps)
                result = await asyncio.to_thread(agent.run, step.get("description", ""))
                yield TextDeltaEvent(content=f"[Result]\n{result}\n")
                final_reason = "stop"
        
        yield WorkflowFinishEvent(finish_reason=final_reason)

    async def _call_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        processed_request = await self.pipeline.pre_process(request.model_copy(deep=True), self)
        raw_stream = self._raw_stream_llm(model_key, endpoint, processed_request)
        async for event in self.pipeline.post_process_stream(raw_stream, processed_request, self):
            yield event

    async def _raw_stream_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        payload = request
        payload.model = self.settings.llm.models.get(model_key, "default")
        payload.stream = True

        try:
            json_payload = payload.model_dump(exclude_none=True)
            final_finish_reason = "stop"
            
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
                            
        except Exception as e:
            logger.error(f"[BROWNIE DEBUG] Streaming Exception: {e}")
            yield ErrorEvent(message=f"Connection Error: {e}")

    async def shutdown(self):
        for client in self.mcp_clients.values():
            await client.stop()
        await self.http_client.aclose()