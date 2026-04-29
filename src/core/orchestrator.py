import os
import yaml
import json
import asyncio
import logging
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import AsyncExitStack

import httpx
from loguru import logger
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


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
    def __init__(self, settings: Settings):
        self.settings = settings
        self.endpoint = settings.llm.interlocutor_endpoint
        self.model_name = settings.llm.models.get("interlocutor", "default")
        self.timeout = settings.llm.timeout_sec
        
        self.coder_keywords = [
            "コード", "修正", "実装", "バグ", "エラー", "リファクタ",
            "スクリプト", "ファイル", "プログラム", "作って", "追加して"
        ]
        logger.info("Lightweight LLM Router initialized.")

    async def route(self, query: str) -> str:
        if not query:
            return "interlocutor"

        for kw in self.coder_keywords:
            if kw in query:
                logger.debug(f"Router: Keyword match '{kw}' -> coder")
                return "coder"

        prompt = (
            "Classify the following user input into one of two categories:\n"
            "1. 'coder' (requires writing/modifying code, file operations, debugging)\n"
            "2. 'interlocutor' (general conversation, greetings, simple questions)\n\n"
            "Output ONLY the category name ('coder' or 'interlocutor'). No other text.\n\n"
            f"Input: {query}"
        )

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0.0
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.endpoint}/chat/completions", json=payload)
                resp.raise_for_status()
                result = resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
                return "coder" if "coder" in answer else "interlocutor"
        except Exception as e:
            logger.error(f"Router LLM Error: {e}. Defaulting to interlocutor.")
            return "interlocutor"


# ==========================================
# 3. Gateway Client
# ==========================================
class GatewayClient:
    def __init__(self, command: str = "mcp-gateway", args: Optional[List[str]] = None):
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
    def __init__(self, mcp_tool_def, mcp_client, loop):
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
        self.mcp_config_path = self.project_root / "mcp_config.json"
        
        self.system_prompt = self._load_system_prompt()
        self.router = Router(settings=self.settings)
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        
        gateway_cmd = "mcp-gateway"
        gateway_args = []
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                    if "mcp-routing-gateway" in servers:
                        gateway_cmd = servers["mcp-routing-gateway"].get("command", gateway_cmd)
                        gateway_args = servers["mcp-routing-gateway"].get("args", gateway_args)
            except Exception as e:
                logger.error(f"Failed to load mcp_config.json: {e}")

        self.mcp_client = GatewayClient(command=os.getenv("BROWNIE_GATEWAY_CMD", gateway_cmd), args=gateway_args)

    async def start(self):
        await self.mcp_client.start()
        try:
            for key in ["interlocutor", "coder"]:
                model = self.settings.llm.models.get(key)
                if model:
                    port = urlparse(getattr(self.settings.llm, f"{key}_endpoint")).port or 8080
                    await self.mcp_client.call_tool("launch_llm_server", {"model_name": model, "port": port})
        except Exception as e:
            logger.error(f"Auto-launch failed: {e}")
        logger.info("✅ Orchestrator: Hybrid-Workflow engine ready.")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "You are BROWNIE."

    async def submit_chat_completion(self, request_data: Dict[str, Any]):
        return await self.orchestrate(request_data)

    async def orchestrate(self, request_data: Dict[str, Any]):
        messages = request_data.get("messages", [])
        user_input = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        actor = await self.router.route(user_input)
        logger.info(f"Selected Actor: {actor}")
        return await self._run_workflow(actor, request_data)

    async def _run_workflow(self, actor: str, request_data: Dict[str, Any]):
        workflow_path = self.workflows_dir / f"{actor}.yaml"
        stream = request_data.get("stream", False)
        if not workflow_path.exists():
            return self._stream_error("Workflow not found") if stream else self._error_response("Workflow not found")

        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                steps = yaml.safe_load(f).get("steps", [])
        except Exception as e:
            return self._stream_error(str(e)) if stream else self._error_response(str(e))

        mcp_tools = await self.mcp_client.fetch_tools()
        loop = asyncio.get_running_loop()

        if stream:
            async def stream_generator():
                for i, step in enumerate(steps):
                    model_key = step.get("model_key")
                    if not model_key:
                        logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                        yield {"choices": [{"delta": {"content": f"ERROR: Step {i+1} is missing required 'model_key'."}, "finish_reason": "error"}]}
                        return

                    endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
                    
                    if step.get("type") == "llm_chat":
                        async for chunk in await self._call_llm(model_key, endpoint, request_data):
                            yield chunk
                        return
                    
                    elif step.get("type") == "agent_task":
                        yield {"choices": [{"delta": {"role": "assistant", "content": f"\n[Step {i+1} Start]\n"}}]}
                        agent_model = OpenAIServerModel(model_id=self.settings.llm.models.get(model_key), api_base=endpoint, api_key="none")
                        agent = ToolCallingAgent(tools=[MCPVirtualTool(t, self.mcp_client, loop) for t in mcp_tools], model=agent_model)
                        result = await asyncio.to_thread(agent.run, step.get("description", ""))
                        yield {"choices": [{"delta": {"content": f"[Result]\n{result}\n"}}]}
                
                yield {"choices": [{"delta": {"content": "\nワークフロー完了。\n"}, "finish_reason": "stop"}]}
            return stream_generator()
        else:
            final_result = ""
            for i, step in enumerate(steps):
                model_key = step.get("model_key")
                if not model_key:
                    logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                    return self._error_response(f"Step {i+1} is missing required 'model_key'.")

                endpoint = getattr(self.settings.llm, f"{model_key}_endpoint")
                
                if step.get("type") == "llm_chat":
                    return await self._call_llm(model_key, endpoint, request_data)
                
                elif step.get("type") == "agent_task":
                    agent_model = OpenAIServerModel(model_id=self.settings.llm.models.get(model_key), api_base=endpoint, api_key="none")
                    agent = ToolCallingAgent(tools=[MCPVirtualTool(t, self.mcp_client, loop) for t in mcp_tools], model=agent_model)
                    try:
                        result = await asyncio.to_thread(agent.run, step.get("description", ""))
                        final_result = str(result)
                    except Exception as e:
                        logger.error(f"SDK Error: {e}")
                        return self._error_response(f"Task Failed: {e}")

            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": f"ワークフロー完了。\n\n最終結果:\n{final_result}"
                    },
                    "finish_reason": "stop"
                }]
            }

    async def _call_llm(self, model_key: str, endpoint: str, request_data: Dict[str, Any]):
        payload = request_data.copy()
        payload["model"] = self.settings.llm.models.get(model_key, "default")
        
        # システムプロンプトを先頭に挿入
        messages = [{"role": "system", "content": self.system_prompt}] + payload.get("messages", [])
        payload["messages"] = messages

        if request_data.get("stream"):
            async def generator():
                async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                    async with client.stream("POST", f"{endpoint}/chat/completions", json=payload) as resp:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: ") and line[6:] != "[DONE]":
                                yield json.loads(line[6:])
            return generator()
        else:
            async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                resp = await client.post(f"{endpoint}/chat/completions", json=payload)
                return resp.json()

    def _error_response(self, msg: str):
        return {"choices": [{"message": {"role": "assistant", "content": f"ERROR: {msg}"}, "finish_reason": "error"}]}

    def _stream_error(self, msg: str):
        async def gen(): yield {"choices": [{"delta": {"content": f"ERROR: {msg}"}, "finish_reason": "error"}]}
        return gen()

    async def shutdown(self):
        await self.mcp_client.stop()
        await self.http_client.aclose()