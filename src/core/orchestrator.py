# src/core/orchestrator.py
import os
import time
import yaml
import json
import asyncio
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator
from contextlib import AsyncExitStack

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# smolagents logic
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

# 修正: インポートパスを現在の環境 (internal_schema) に適合
from core.internal_schema import InternalAgentRequest, InternalMessage, InternalTool
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

class LLMSettings(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    interlocutor_endpoint: str = Field(default="http://localhost:8080/v1")
    coder_endpoint: str = Field(default="http://localhost:8081/v1")
    timeout_sec: int = Field(default=120)
    launcher_client: Optional[str] = Field(default="mlx-launcher")
    launcher_tool: Optional[str] = Field(default="launch_llm_server")

class WorkspaceSettings(BaseModel):
    # 修正: デフォルト値を cingulater に変更
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
# 2. Router
# ==========================================
class Router:
    def __init__(self, settings: Settings, workflows_dir: Path, orchestrator: "Orchestrator"):
        self.settings = settings
        self.workflows_dir = workflows_dir
        self.orchestrator = orchestrator
        logger.info("Intent Reranker Router initialized.")

    async def route(self, messages: List[InternalMessage]) -> tuple[str, list]:
        actors = []
        documents = []
        workflows = {}
        
        workflow_paths = [self.orchestrator.project_root / "src" / "core" / "interlocutor.yaml"]
        workflow_paths.extend(self.workflows_dir.glob("*.yaml"))
        
        for p in workflow_paths:
            if not p.exists():
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    wf_data = yaml.safe_load(f) or {}
                    name = wf_data.get("name", p.stem)
                    desc = wf_data.get("description", f"Expert named {name}")
                    steps = wf_data.get("steps", [])
                    
                    if name not in actors:
                        actors.append(name)
                        documents.append(desc)
                        workflows[name] = steps
            except Exception as e:
                logger.error(f"Failed to load workflow {p}: {e}")
                
        default_actor = "interlocutor"
        default_steps = workflows.get(default_actor, [])

        if not actors:
            return default_actor, default_steps
            
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
                    return selected_actor, workflows.get(selected_actor, [])
                else:
                    logger.warning("mcp-reranker returned empty results. Defaulting to interlocutor.")
                    return default_actor, default_steps
            else:
                logger.warning("mcp-reranker client not connected. Defaulting to interlocutor.")
                return default_actor, default_steps
            
        except Exception as e:
            logger.error(f"Router Reranker Error: {e}. Defaulting to interlocutor.")
            return default_actor, default_steps


# ==========================================
# 3. Core MCP Client & RemoteMCPTool
# ==========================================
class RemoteMCPTool(Tool):
    """MCP Clientのツールをsmolagents.Toolとしてラップするクラス"""
    def __init__(self, client: "MCPClient", tool_schema: Dict[str, Any]):
        self.client = client
        self.name = tool_schema.get("name")
        self.description = tool_schema.get("description")
        self.inputs = tool_schema.get("inputSchema", {}).get("properties", {})
        self.output_type = "string"

    def forward(self, **kwargs) -> str:
        # NOTE: forward is synchronous, so we run the async tool call in a loop
        import asyncio
        loop = asyncio.get_event_loop()
        
        # Determine if we're in a running loop or not
        if loop.is_running():
            # This is tricky because smolagents is synchronous
            # and our MCP client is async. We use nest_asyncio.
            import nest_asyncio
            nest_asyncio.apply()
            
        return asyncio.run(self.client.call_tool(self.name, kwargs))

class MCPClient:
    def __init__(self, command: str, args: Optional[List[str]] = None):
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
            logger.info(f"✅ Successfully connected to MCP Core Tool via {self.command}.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to MCP Core Tool: {e}")
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
            raise ValueError("MCP Client is not connected.")
        result = await self.session.call_tool(tool_name, arguments)
        output = ""
        for content in result.content:
            if isinstance(content, types.TextContent):
                output += content.text + "\n"
        return output.strip()


# ==========================================
# 4. Core Orchestrator
# ==========================================
class Orchestrator:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        # 修正: ディレクトリおよびファイル名を Cingulater に適合
        self.workflows_dir = self.project_root / "workflows"
        self.system_prompt_path = self.project_root / ".cingulater" / "system_prompt.md"
        self.mcp_config_path = self.project_root / "mcp_config.json"
        
        self.system_prompt = self._load_system_prompt()
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.router = Router(settings=self.settings, workflows_dir=self.workflows_dir, orchestrator=self)
        self.llm_client = OpenAILLMClient()
        
        self.llm_pipeline = InterceptorPipeline([
            LoggingInterceptor(),
            ContextLimitInterceptor(max_messages=20),
            SystemPromptInterceptor(),
            ModelConfigurationInterceptor(),
            ToolHallucinationInterceptor(),
            ReflectionInterceptor(),
            ErrorHandlingInterceptor()
        ])

        self.workflow_pipeline = WorkflowInterceptorPipeline([
            WorkflowLoadInterceptor(),
            ToolFetchInterceptor(),
            WorkflowExecutionInterceptor()
        ])
        
        self.mcp_clients: Dict[str, MCPClient] = {}
        
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                    for name, config in servers.items():
                        cmd = config.get("command")
                        args = config.get("args", [])
                        
                        if cmd:
                            self.mcp_clients[name] = MCPClient(command=cmd, args=args)
            except Exception as e:
                logger.error(f"Failed to load {self.mcp_config_path.name}: {e}")

        if not self.mcp_clients:
            logger.info("No internal MCP clients configured in mcp_config.json.")

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
        logger.info("✅ Orchestrator: Cingulater Backend Engine ready.")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        # 修正: デフォルトアイデンティティを CINGULATER に変更
        return "You are CINGULATER, a powerful AI backend."

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
        actor, workflow_steps = await self.router.route(request.messages)
        logger.info(f"Selected Actor: {actor}")
        
        async for event in self.workflow_pipeline.process(actor, request, self, self._raw_run_workflow, workflow_steps=workflow_steps):
            yield event

    async def _raw_run_workflow(self, actor: str, request: InternalAgentRequest, **kwargs) -> AsyncGenerator[AgentEvent, None]:
        steps = kwargs.get("workflow_steps", [])
        fetched_mcp_tools = kwargs.get("fetched_mcp_tools", [])
        final_finish_reason = "stop"

        # Initialize current_messages with the messages from the request
        current_messages = list(request.messages)

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                yield ErrorEvent(message=f"Step {i+1} is missing required 'model_key'.")
                return

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            
            # 1. Merge available MCP tools with any tools in the base request
            step_tools = []
            if request.tools:
                step_tools.extend(request.tools)
            
            for client, tool_schema in fetched_mcp_tools:
                step_tools.append(InternalTool(
                    type="function",
                    function=tool_schema
                ))
            
            # 2. Create the step request, utilizing accumulated history
            step_request = request.model_copy(update={
                "messages": current_messages,
                "tools": step_tools if step_tools else None
            })
            
            # 3. Call the LLM
            full_response_content = ""
            async for event in self._call_llm(model_key, endpoint, step_request):
                if isinstance(event, TextDeltaEvent):
                    full_content = event.content or ""
                    full_response_content += full_content
                
                if isinstance(event, WorkflowFinishEvent):
                    final_finish_reason = event.finish_reason
                
                yield event
            
            # 4. Update conversation history for the NEXT step
            if full_response_content:
                current_messages.append(InternalMessage(
                    role="assistant",
                    content=full_response_content
                ))
            
        yield WorkflowFinishEvent(finish_reason=final_finish_reason)

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

    async def shutdown(self):
        for client in self.mcp_clients.values():
            await client.stop()
        await self.http_client.aclose()