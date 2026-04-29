import os
import time
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
    def __init__(self, settings: Settings, workflows_dir: Path):
        self.settings = settings
        self.workflows_dir = workflows_dir
        self.endpoint = settings.llm.interlocutor_endpoint
        self.model_name = settings.llm.models.get("interlocutor", "default")
        self.timeout = settings.llm.timeout_sec
        logger.info("Dynamic Context-Aware LLM Router initialized.")

    async def route(self, messages: List[Dict[str, Any]]) -> str:
        actors = [p.stem for p in self.workflows_dir.glob("*.yaml")]
        if not actors:
            return "interlocutor"
            
        recent_msgs = messages[-5:]
        history_text = ""
        for m in recent_msgs:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join([p.get("text", "") for p in content if isinstance(p, dict)])
            if content and len(content) > 500:
                content = content[:500] + " ...[truncated]"
            history_text += f"[{role}]: {content}\n"

        prompt = (
            "You are an intelligent routing agent. Based on the conversation history below, "
            "select the most appropriate expert to handle the next user request.\n\n"
            f"Available experts: {', '.join(actors)}\n\n"
            "[Conversation History]\n"
            f"{history_text}\n\n"
            "Rule: Output ONLY the exact name of the chosen expert from the list above. No other text."
        )

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 20,
            "temperature": 0.0
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.endpoint}/chat/completions", json=payload)
                resp.raise_for_status()
                result = resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
                
                for actor in actors:
                    if actor.lower() in answer:
                        return actor
                        
                return "interlocutor"
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
        self.router = Router(settings=self.settings, workflows_dir=self.workflows_dir)
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

    def _create_error_chunk(self, msg: str, model: str = "default"):
        return {
            "id": f"chatcmpl-err-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": f"\n\n[Brownie Error: {msg}]\n\n"},
                    "finish_reason": "error"
                }
            ]
        }

    async def submit_chat_completion(self, request_data: Dict[str, Any]):
        request_data.setdefault("stream", True)
        return await self.orchestrate(request_data)

    async def orchestrate(self, request_data: Dict[str, Any]):
        messages = request_data.get("messages", [])
        actor = await self.router.route(messages)
        logger.info(f"Selected Actor: {actor}")
        return await self._run_workflow(actor, request_data)

    async def _run_workflow(self, actor: str, request_data: Dict[str, Any]):
        workflow_path = self.workflows_dir / f"{actor}.yaml"
        client_wants_stream = request_data.get("stream", True)
        
        if not workflow_path.exists():
            return self._stream_error("Workflow not found") if client_wants_stream else self._error_response("Workflow not found")

        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                steps = yaml.safe_load(f).get("steps", [])
        except Exception as e:
            return self._stream_error(str(e)) if client_wants_stream else self._error_response(str(e))

        mcp_tools = await self.mcp_client.fetch_tools()
        loop = asyncio.get_running_loop()

        async def run_steps_generator():
            for i, step in enumerate(steps):
                model_key = step.get("model_key")
                if not model_key:
                    logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                    yield self._create_error_chunk(f"Step {i+1} is missing required 'model_key'.")
                    return

                endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
                
                if step.get("type") == "llm_chat":
                    async for chunk in await self._call_llm(model_key, endpoint, request_data):
                        yield chunk
                    return
                
                elif step.get("type") == "agent_task":
                    yield {"id": f"chatcmpl-{int(time.time())}", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"\n[Step {i+1} Start]\n"}}]}
                    agent_model = OpenAIServerModel(model_id=self.settings.llm.models.get(model_key), api_base=endpoint, api_key="none")
                    agent = ToolCallingAgent(tools=[MCPVirtualTool(t, self.mcp_client, loop) for t in mcp_tools], model=agent_model)
                    result = await asyncio.to_thread(agent.run, step.get("description", ""))
                    yield {"id": f"chatcmpl-{int(time.time())}", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": f"[Result]\n{result}\n"}}]}
            
            yield {"id": f"chatcmpl-{int(time.time())}", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "\nワークフロー完了。\n"}, "finish_reason": "stop"}]}

        gen = run_steps_generator()

        if client_wants_stream:
            return gen
        else:
            return await self._assemble_stream_to_dict(gen)

    async def _assemble_stream_to_dict(self, generator) -> Dict[str, Any]:
        """ストリームのチャンクを結合して1つの完全なJSON(非ストリーム形式)にアセンブルする"""
        full_content = ""
        tool_calls_dict = {}
        last_chunk = None
        finish_reason = "stop"

        async for chunk in generator:
            choice = chunk.get("choices", [{}])[0]
            chunk_fr = choice.get("finish_reason")
            
            # TypeError対策: chunk_fr が None の場合を考慮し、安全に文字列として "error" をチェックする
            if chunk_fr and isinstance(chunk_fr, str) and "error" in chunk_fr:
                err_msg = choice.get("delta", {}).get("content", "Unknown Error")
                return self._error_response(err_msg)

            last_chunk = chunk
            delta = choice.get("delta", {})
            
            if "content" in delta and isinstance(delta["content"], str):
                full_content += delta["content"]
                
            if "tool_calls" in delta:
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_dict:
                        tool_calls_dict[idx] = {
                            "id": tc.get("id", f"call_{idx}"),
                            "type": "function",
                            "function": {"name": tc.get("function", {}).get("name", ""), "arguments": ""}
                        }
                    if "function" in tc and "arguments" in tc["function"]:
                        tool_calls_dict[idx]["function"]["arguments"] += tc["function"]["arguments"]

            if chunk_fr:
                finish_reason = chunk_fr

        if not last_chunk:
            return self._error_response("Empty response from LLM")

        message = {"role": "assistant"}
        if full_content:
            message["content"] = full_content
        else:
            message["content"] = None

        if tool_calls_dict:
            message["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

        return {
            "id": last_chunk.get("id", f"chatcmpl-{int(time.time())}"),
            "object": "chat.completion",
            "created": last_chunk.get("created", int(time.time())),
            "model": last_chunk.get("model", "default"),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason
                }
            ]
        }

    async def _call_llm(self, model_key: str, endpoint: str, request_data: Dict[str, Any]):
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
        
        logger.info(f"[BROWNIE DEBUG] ======= API Request Started =======")
        logger.info(f"[BROWNIE DEBUG] Target Model Key: {model_key}")
        logger.info(f"[BROWNIE DEBUG] Client requested stream: {request_data.get('stream', True)}")
        logger.info(f"[BROWNIE DEBUG] Backend internal stream forced: True")
        logger.info(f"[BROWNIE DEBUG] Client provided {len(available_tool_names)} tools.")
        logger.info(f"[BROWNIE DEBUG] Available Tools: {available_tool_names}")
        
        messages = list(payload.get("messages", []))
        if messages and messages[0]["role"] == "system":
            new_sys = dict(messages[0])
            new_sys["content"] = self.system_prompt + "\n\n" + new_sys.get("content", "")
            messages[0] = new_sys
        else:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        payload["messages"] = messages

        async def generator():
            try:
                async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                    async with client.stream("POST", f"{endpoint}/chat/completions", json=payload) as resp:
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            logger.error(f"[BROWNIE DEBUG] LLM Server Error: {resp.status_code} - {error_text}")
                            yield self._create_error_chunk(f"LLM Error {resp.status_code}: {error_text.decode('utf-8', errors='ignore')}", payload["model"])
                            return
                        
                        content_type = resp.headers.get("content-type", "")
                        
                        # === JSONフォールバック処理 ===
                        if "application/json" in content_type:
                            logger.info("[BROWNIE DEBUG] LLM responded with one-shot JSON (Stream fallback)")
                            body = await resp.aread()
                            try:
                                full_json = json.loads(body)
                                base_chunk = {
                                    "id": full_json.get("id", f"chatcmpl-{int(time.time())}"),
                                    "object": "chat.completion.chunk",
                                    "created": full_json.get("created", int(time.time())),
                                    "model": payload["model"]
                                }
                                
                                for choice in full_json.get("choices", []):
                                    message = choice.get("message", {})
                                    delta = {}
                                    if "role" in message: delta["role"] = message["role"]
                                    if "content" in message: delta["content"] = message["content"]
                                    
                                    if "tool_calls" in message:
                                        delta_tcs = []
                                        for idx, tc in enumerate(message["tool_calls"]):
                                            tc_copy = dict(tc)
                                            tc_copy["index"] = idx
                                            func_name = tc_copy.get("function", {}).get("name")
                                            logger.info(f"[BROWNIE DEBUG] JSON contains tool call: {func_name}")
                                            
                                            if func_name not in available_tool_names and available_tool_names:
                                                fallback_name = available_tool_names[0]
                                                logger.warning(f"[BROWNIE DEBUG] Tool '{func_name}' is NOT available! Rewriting to '{fallback_name}'.")
                                                tc_copy["function"]["name"] = fallback_name
                                                try:
                                                    args = json.loads(tc_copy["function"]["arguments"])
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
                                                    
                                                    tc_copy["function"]["arguments"] = json.dumps(new_args)
                                                except Exception as e:
                                                    logger.error(f"Failed to rewrite JSON tool arguments: {e}")
                                            delta_tcs.append(tc_copy)
                                        delta["tool_calls"] = delta_tcs
                                    
                                    chunk1 = dict(base_chunk)
                                    chunk1["choices"] = [{"index": choice.get("index", 0), "delta": delta, "finish_reason": None}]
                                    yield chunk1
                                    
                                    chunk2 = dict(base_chunk)
                                    chunk2["choices"] = [{"index": choice.get("index", 0), "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}]
                                    yield chunk2
                            except Exception as e:
                                logger.error(f"Failed to parse fallback JSON: {e}")
                                yield self._create_error_chunk("Failed to parse fallback JSON", payload["model"])
                        
                        # === ストリーム処理 ===
                        else:
                            has_tool_calls = False
                            full_content = ""
                            last_id = f"chatcmpl-{int(time.time())}"
                            
                            logger.info("[BROWNIE DEBUG] LLM Stream started...")

                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if not line:
                                    continue
                                    
                                if line == "data: [DONE]":
                                    logger.info("[BROWNIE DEBUG] Stream native [DONE] received.")
                                    break

                                if line.startswith("data: "):
                                    try:
                                        chunk = json.loads(line[6:])
                                        last_id = chunk.get("id", last_id)
                                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                                        
                                        if "tool_calls" in delta:
                                            has_tool_calls = True
                                            for tc in delta["tool_calls"]:
                                                fn_name = tc.get("function", {}).get("name")
                                                if fn_name:
                                                    logger.info(f"[BROWNIE DEBUG] LLM natively streaming tool call: {fn_name}")
                                                    if fn_name not in available_tool_names:
                                                        logger.warning(f"[BROWNIE DEBUG] WARNING! LLM is calling '{fn_name}' which is NOT in available tools!")

                                        if "content" in delta and isinstance(delta["content"], str):
                                            full_content += delta["content"]
                                        
                                        yield chunk
                                    except json.JSONDecodeError:
                                        pass
                                        
                            # 【反芻ステップ】
                            if full_content and not has_tool_calls and available_tools_dict:
                                logger.info("[BROWNIE DEBUG] --- Reflection Phase Started ---")
                                logger.info(f"[BROWNIE DEBUG] Pure text length: {len(full_content)} chars.")
                                
                                prompt = (
                                    "You are a Tool Selection AI. Based on the assistant's final response below, "
                                    "choose the most appropriate tool to conclude the interaction.\n\n"
                                    f"[Assistant Response]\n\"{full_content[-1000:]}\"\n\n"
                                    f"Available tools: {', '.join(available_tool_names)}\n\n"
                                    "Rule: If the assistant asks a question, choose a tool related to 'ask' or 'question'. "
                                    "Otherwise, choose a tool related to 'complete' or 'result'.\n"
                                    "Output ONLY the exact tool name."
                                )
                                ref_payload = {
                                    "model": payload["model"],
                                    "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 20,
                                    "temperature": 0.0
                                }
                                
                                selected_tool = available_tool_names[0]
                                try:
                                    async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as ref_client:
                                        ref_resp = await ref_client.post(f"{endpoint}/chat/completions", json=ref_payload)
                                        if ref_resp.status_code == 200:
                                            ans = ref_resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                                            logger.info(f"[BROWNIE DEBUG] Reflection AI Raw Answer: '{ans}'")
                                            for tn in available_tool_names:
                                                if tn.lower() in ans.lower():
                                                    selected_tool = tn
                                                    break
                                        else:
                                            logger.warning(f"[BROWNIE DEBUG] Reflection LLM failed with {ref_resp.status_code}")
                                except Exception as e:
                                    logger.error(f"[BROWNIE DEBUG] Reflection AI Error: {e}")
                                    
                                logger.info(f"[BROWNIE DEBUG] Reflection Phase Selected Tool: '{selected_tool}'")
                                    
                                tool_schema = available_tools_dict[selected_tool]
                                props = tool_schema.get("parameters", {}).get("properties", {})
                                reqs = tool_schema.get("parameters", {}).get("required", [])
                                
                                args = {}
                                for req in reqs:
                                    if "question" in req.lower() or "ask" in req.lower():
                                        args[req] = "Please answer the question provided in the chat."
                                    elif "result" in req.lower() or "summary" in req.lower() or "message" in req.lower():
                                        args[req] = "Response provided in chat."
                                    else:
                                        ptype = props.get(req, {}).get("type", "string")
                                        if ptype == "array": args[req] = []
                                        elif ptype == "object": args[req] = {}
                                        elif ptype == "boolean": args[req] = False
                                        elif ptype in ["number", "integer"]: args[req] = 0
                                        else: args[req] = "Completed."
                                        
                                yield {
                                    "id": last_id,
                                    "object": "chat.completion.chunk",
                                    "choices": [{"index": 0, "delta": {
                                        "tool_calls": [{
                                            "index": 0,
                                            "id": f"call_ref_{int(time.time())}",
                                            "type": "function",
                                            "function": {
                                                "name": selected_tool,
                                                "arguments": json.dumps(args)
                                            }
                                        }]
                                    }}]
                                }
                                yield {
                                    "id": last_id,
                                    "object": "chat.completion.chunk",
                                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
                                }
                                logger.info("[BROWNIE DEBUG] Stream gracefully closed via Reflection Tool Call.")
                                logger.info("[BROWNIE DEBUG] =====================================")
                            elif not has_tool_calls:
                                yield {
                                    "id": last_id,
                                    "object": "chat.completion.chunk",
                                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                                }
                                logger.info("[BROWNIE DEBUG] Stream closed naturally without tool calls.")
                                logger.info("[BROWNIE DEBUG] =====================================")
                            else:
                                logger.info("[BROWNIE DEBUG] Stream closed naturally with native tool calls.")
                                logger.info("[BROWNIE DEBUG] =====================================")
            except Exception as e:
                logger.error(f"[BROWNIE DEBUG] Streaming Exception: {e}")
                yield self._create_error_chunk(f"Connection Error: {e}", payload["model"])

        return generator()

    def _error_response(self, msg: str):
        return {"choices": [{"message": {"role": "assistant", "content": f"ERROR: {msg}"}, "finish_reason": "error"}]}

    def _stream_error(self, msg: str):
        return self._create_error_chunk(msg)

    async def shutdown(self):
        await self.mcp_client.stop()
        await self.http_client.aclose()