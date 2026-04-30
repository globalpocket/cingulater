# src/core/orchestrator.py
import os
import time
import yaml
import json
import asyncio
import logging
import threading
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator
from contextlib import AsyncExitStack

import httpx
from loguru import logger
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from sentence_transformers import CrossEncoder

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from core.events import (
    AgentEvent,
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
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
# 2. Intent Reranker Service
# ==========================================
class IntentRerankerService:
    """Rerankerを利用してIntentとドキュメント(説明文)の関連度をスコアリングするシングルトンサービス"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, model_name: str = "BAAI/bge-reranker-v2-m3"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialize(model_name)
        return cls._instance

    def _initialize(self, model_name: str):
        logger.info(f"Initializing IntentRerankerService with model: {model_name}")
        self.reranker = CrossEncoder(model_name)

    def rerank(self, query: str, documents: List[str]) -> List[Dict[str, Any]]:
        """クエリ(query)と複数の候補(documents)の関連度をスコアリングして降順で返す"""
        if not documents:
            return []
        
        pairs = [[query, doc] for doc in documents]
        scores = self.reranker.predict(pairs)
        
        results = [{"document": doc, "score": float(score)} for doc, score in zip(documents, scores)]
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return results


# ==========================================
# 3. Router
# ==========================================
class Router:
    def __init__(self, settings: Settings, workflows_dir: Path, orchestrator: "Orchestrator"):
        self.settings = settings
        self.workflows_dir = workflows_dir
        self.orchestrator = orchestrator
        logger.info("Intent Reranker Router initialized.")

    async def route(self, messages: List[Dict[str, Any]]) -> str:
        actors = []
        documents = []
        
        for p in self.workflows_dir.glob("*.yaml"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    wf_data = yaml.safe_load(f) or {}
                    name = wf_data.get("name", p.stem)
                    desc = wf_data.get("description", f"Expert named {name}")
                    actors.append(name)
                    # 余計な接頭辞を入れず、純粋なDescriptionを比較対象にする
                    documents.append(desc)
            except Exception as e:
                logger.error(f"Failed to load workflow {p}: {e}")
                
        if not actors:
            return "interlocutor"
            
        recent_msgs = messages[-5:]
        history_text = ""
        for m in recent_msgs:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join([part.get("text", "") for part in content if isinstance(part, dict)])
            if content and len(content) > 500:
                content = content[:500] + " ...[truncated]"
            history_text += f"[{role}]: {content}\n"

        # LLMを用いて会話履歴からIntentを抽出
        intent = await self.orchestrator._extract_intent(history_text)
        
        try:
            reranker = IntentRerankerService()
            # 抽出したIntentをそのままクエリとして使用する
            results = await asyncio.to_thread(reranker.rerank, intent, documents)
            
            best_doc = results[0]["document"]
            best_idx = documents.index(best_doc)
            selected_actor = actors[best_idx]
            
            logger.info(f"Router selected '{selected_actor}' with score {results[0]['score']:.4f} (Intent: {intent})")
            return selected_actor
            
        except Exception as e:
            logger.error(f"Router Reranker Error: {e}. Defaulting to interlocutor.")
            return "interlocutor"


# ==========================================
# 4. Gateway Client
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
# 5. Core Orchestrator
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
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.router = Router(settings=self.settings, workflows_dir=self.workflows_dir, orchestrator=self)
        
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

    async def process_workflow(self, request_data: Dict[str, Any]) -> AsyncGenerator[AgentEvent, None]:
        messages = request_data.get("messages", [])
        actor = await self.router.route(messages)
        logger.info(f"Selected Actor: {actor}")
        
        async for event in self._run_workflow(actor, request_data):
            yield event

    async def _run_workflow(self, actor: str, request_data: Dict[str, Any]) -> AsyncGenerator[AgentEvent, None]:
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

        mcp_tools = await self.mcp_client.fetch_tools()
        loop = asyncio.get_running_loop()

        final_reason = "stop"

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                yield ErrorEvent(message=f"Step {i+1} is missing required 'model_key'.")
                return

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            
            if step.get("type") == "llm_chat":
                async for event in self._call_llm(model_key, endpoint, request_data):
                    if isinstance(event, WorkflowFinishEvent):
                        final_reason = event.finish_reason
                    else:
                        yield event
            
            elif step.get("type") == "agent_task":
                yield TextDeltaEvent(content=f"\n[Step {i+1} Start]\n")
                agent_model = OpenAIServerModel(model_id=self.settings.llm.models.get(model_key), api_base=endpoint, api_key="none")
                agent = ToolCallingAgent(tools=[MCPVirtualTool(t, self.mcp_client, loop) for t in mcp_tools], model=agent_model)
                result = await asyncio.to_thread(agent.run, step.get("description", ""))
                yield TextDeltaEvent(content=f"[Result]\n{result}\n")
                final_reason = "stop"
        
        yield WorkflowFinishEvent(finish_reason=final_reason)

    async def _call_llm(self, model_key: str, endpoint: str, request_data: Dict[str, Any]) -> AsyncGenerator[AgentEvent, None]:
        payload = request_data.copy()
        payload["model"] = self.settings.llm.models.get(model_key, "default")
        
        if not payload.get("max_tokens"):
            payload["max_tokens"] = 8192
            
        payload["stream"] = True
            
        available_tools_dict = {
            t.get("function", {}).get("name"): t.get("function", {})
            for t in request_data.get("tools", []) 
            if isinstance(t, dict) and t.get("function")
        }
        available_tool_names = list(available_tools_dict.keys())
        
        messages = list(payload.get("messages", []))
        if messages and messages[0]["role"] == "system":
            new_sys = dict(messages[0])
            new_sys["content"] = self.system_prompt + "\n\n" + new_sys.get("content", "")
            messages[0] = new_sys
        else:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        payload["messages"] = messages

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                async with client.stream("POST", f"{endpoint}/chat/completions", json=payload) as resp:
                    if resp.status_code != 200:
                        error_text = await resp.aread()
                        yield ErrorEvent(message=f"LLM Error {resp.status_code}: {error_text.decode('utf-8', errors='ignore')}")
                        return
                    
                    content_type = resp.headers.get("content-type", "")
                    
                    if "application/json" in content_type:
                        body = await resp.aread()
                        try:
                            full_json = json.loads(body)
                            for choice in full_json.get("choices", []):
                                message = choice.get("message", {})
                                
                                if "content" in message and message["content"]:
                                    yield TextDeltaEvent(content=message["content"])
                                
                                if "tool_calls" in message:
                                    for idx, tc in enumerate(message["tool_calls"]):
                                        func_name = tc.get("function", {}).get("name")
                                        args_str = tc.get("function", {}).get("arguments", "{}")
                                        tc_id = tc.get("id", f"call_{idx}")
                                        
                                        if func_name not in available_tool_names and available_tool_names:
                                            fallback_name = available_tool_names[0]
                                            logger.warning(f"[BROWNIE DEBUG] Tool '{func_name}' is NOT available! Rewriting to '{fallback_name}'.")
                                            try:
                                                args = json.loads(args_str)
                                                text_val = ""
                                                for v in args.values():
                                                    if isinstance(v, str) and len(v) > len(text_val):
                                                        text_val = v
                                                if not text_val: text_val = str(args)
                                                
                                                tool_schema = available_tools_dict[fallback_name]
                                                fallback_props = tool_schema.get("parameters", {}).get("properties", {})
                                                fallback_required = tool_schema.get("parameters", {}).get("required", [])
                                                
                                                fallback_param_name = "text"
                                                for pref in ["result", "question", "message", "content", "response"]:
                                                    if pref in fallback_props:
                                                        fallback_param_name = pref
                                                        break
                                                else:
                                                    if fallback_props: fallback_param_name = list(fallback_props.keys())[0]

                                                new_args = {fallback_param_name: text_val}
                                                for req in fallback_required:
                                                    if req != fallback_param_name:
                                                        ptype = fallback_props.get(req, {}).get("type", "string")
                                                        if ptype == "array": new_args[req] = []
                                                        elif ptype == "object": new_args[req] = {}
                                                        elif ptype == "boolean": new_args[req] = False
                                                        elif ptype in ["number", "integer"]: new_args[req] = 0
                                                        else: new_args[req] = ""
                                                
                                                yield SystemToolCallEvent(index=idx, id=tc_id, tool_name=fallback_name, arguments=new_args)
                                                continue
                                            except Exception:
                                                pass
                                                
                                        yield ToolCallStartEvent(index=idx, id=tc_id, tool_name=func_name)
                                        yield ToolCallDeltaEvent(index=idx, arguments=args_str)
                                        
                                finish_reason = choice.get("finish_reason", "stop")
                                yield WorkflowFinishEvent(finish_reason=finish_reason)
                                
                        except Exception as e:
                            yield ErrorEvent(message=f"Failed to parse fallback JSON: {e}")
                    
                    else:
                        has_tool_calls = False
                        full_content = ""
                        final_finish_reason = "stop"
                        
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line or line == "data: [DONE]":
                                continue
                                
                            if line.startswith("data: "):
                                try:
                                    chunk = json.loads(line[6:])
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    
                                    if "content" in delta and isinstance(delta["content"], str) and delta["content"]:
                                        full_content += delta["content"]
                                        yield TextDeltaEvent(content=delta["content"])
                                    
                                    if "tool_calls" in delta:
                                        has_tool_calls = True
                                        for tc in delta["tool_calls"]:
                                            idx = tc.get("index", 0)
                                            fn_name = tc.get("function", {}).get("name")
                                            fn_args = tc.get("function", {}).get("arguments")
                                            tc_id = tc.get("id", f"call_{idx}")
                                            
                                            if fn_name:
                                                yield ToolCallStartEvent(index=idx, id=tc_id, tool_name=fn_name)
                                            if fn_args:
                                                yield ToolCallDeltaEvent(index=idx, arguments=fn_args)
                                                
                                    chunk_fr = chunk.get("choices", [{}])[0].get("finish_reason")
                                    if chunk_fr:
                                        final_finish_reason = chunk_fr
                                        
                                except json.JSONDecodeError:
                                    pass
                                    
                        # 【反芻ステップ】テキストのみが返却され、ツール呼び出しがなかった場合のフォールバック (Rerankerを使用)
                        if full_content and not has_tool_calls and available_tools_dict:
                            logger.info("[BROWNIE DEBUG] --- Reflection Phase Started (Using Reranker) ---")
                            
                            intent = await self._extract_intent(full_content[-1000:])
                            docs = []
                            for tn in available_tool_names:
                                desc = available_tools_dict[tn].get("description", "No description provided")
                                docs.append(desc)
                            
                            selected_tool = available_tool_names[0]
                            try:
                                reranker = IntentRerankerService()
                                results = await asyncio.to_thread(reranker.rerank, intent, docs)
                                best_doc = results[0]["document"]
                                best_idx = docs.index(best_doc)
                                selected_tool = available_tool_names[best_idx]
                                logger.info(f"[BROWNIE DEBUG] Reflection selected tool '{selected_tool}' with score {results[0]['score']:.4f} (Intent: {intent})")
                            except Exception as e:
                                logger.error(f"[BROWNIE DEBUG] Reflection Reranker Error: {e}")
                                
                            tool_schema = available_tools_dict[selected_tool]
                            props = tool_schema.get("parameters", {}).get("properties", {})
                            reqs = tool_schema.get("parameters", {}).get("required", [])
                            
                            args = {}
                            for req in reqs:
                                req_lower = req.lower()
                                if any(k in req_lower for k in ["question", "ask", "result", "summary", "message", "text", "content", "response"]):
                                    args[req] = full_content.strip()
                                else:
                                    ptype = props.get(req, {}).get("type", "string")
                                    if ptype == "array": args[req] = []
                                    elif ptype == "object": args[req] = {}
                                    elif ptype == "boolean": args[req] = False
                                    elif ptype in ["number", "integer"]: args[req] = 0
                                    else: args[req] = full_content.strip()
                            
                            tc_id = f"call_ref_{int(time.time())}"
                            yield SystemToolCallEvent(
                                index=0, 
                                id=tc_id, 
                                tool_name=selected_tool,
                                arguments=args
                            )
                            final_finish_reason = "tool_calls"
                            
                        yield WorkflowFinishEvent(finish_reason=final_finish_reason)
                            
        except Exception as e:
            logger.error(f"[BROWNIE DEBUG] Streaming Exception: {e}")
            yield ErrorEvent(message=f"Connection Error: {e}")

    async def shutdown(self):
        await self.mcp_client.stop()
        await self.http_client.aclose()