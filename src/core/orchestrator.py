# src/core/orchestrator.py
import os
import json
import time
import asyncio
import datetime
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

from core.schema import InternalAgentRequest
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


# ==========================================
# 1. Config & Settings
# ==========================================
class AgentSettings(BaseModel):
    max_retries: int = Field(default=3)
    single_task_mode: bool = Field(default=False)

class LLMSettings(BaseModel):
    interlocutor_endpoint: str = Field(default="http://localhost:8080/v1")
    timeout_sec: int = Field(default=300)
    launcher_client: Optional[str] = Field(default="mlx-launcher")
    launcher_tool: Optional[str] = Field(default="launch_llm_server")

class Settings(BaseSettings):
    agent: AgentSettings = Field(default_factory=AgentSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    @classmethod
    def load(cls, config_path: str) -> "Settings":
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                import yaml
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
        self._stop_event.clear()
        self._init_event.clear()
        self._task = asyncio.create_task(self._run())
        
        await self._init_event.wait()
        if not self.session:
            logger.error(f"Failed to initialize MCP session for {self.command}.")

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
                    
                    await self._stop_event.wait()
        except Exception as e:
            logger.error(f"❌ MCP session error ({self.command}): {e}")
            self._init_event.set()
        finally:
            self.session = None

    async def stop(self):
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

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
# 3. Core Orchestrator (Prompt Chaining Pipeline)
# ==========================================
class Orchestrator:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.mcp_config_path = self.project_root / "mcp_config.json"
        self.gateway_log_path = self.project_root / "logs" / "gateway.log"
        
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.llm_client = OpenAILLMClient()
        self.mcp_clients: Dict[str, GatewayClient] = {}
        self.current_loaded_model: Optional[str] = None
        
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                    for name, config in servers.items():
                        if name not in ["mlx-launcher", "mcp-reranker"]:
                            continue
                            
                        cmd = config.get("command")
                        args = config.get("args", [])
                        if cmd:
                            self.mcp_clients[name] = GatewayClient(command=cmd, args=args)
            except Exception as e:
                logger.error(f"Failed to load mcp_config.json: {e}")

    def _log_to_gateway(self, request: InternalAgentRequest, reranker_query: str, available_tools: list, selected_tool: str, tool_args: dict):
        try:
            self.gateway_log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().isoformat()
            with open(self.gateway_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] === GATEWAY LOG ===\n")
                
                last_user_msg = "None"
                if request.messages:
                    for msg in reversed(request.messages):
                        if msg.role == "user":
                            last_user_msg = msg.content or ""
                            if len(last_user_msg) > 500:
                                last_user_msg = last_user_msg[:500] + "...(truncated)"
                            break
                        
                f.write(f"--- LAST USER MESSAGE ---\n{last_user_msg}\n\n")
                
                f.write("--- AVAILABLE TOOLS FROM CLIENT ---\n")
                if available_tools:
                    for t in available_tools:
                        name = t.function.get("name", "unknown")
                        desc = t.function.get("description", "no description")
                        f.write(f"- {name}: {desc}\n")
                f.write("\n")
                
                f.write(f"--- RERANKER QUERY ---\n{reranker_query}\n\n")
                f.write(f"--- SELECTED TOOL ---\n{selected_tool}\n\n")
                f.write(f"--- GENERATED TOOL ARGS ---\n{json.dumps(tool_args, ensure_ascii=False, indent=2)}\n")
                f.write("=========================================\n\n")
        except Exception as e:
            logger.error(f"Failed to write gateway log: {e}")

    async def start(self):
        for name, client in self.mcp_clients.items():
            logger.info(f"Starting MCP Client: {name}")
            await client.start()
            
        logger.info("✅ Orchestrator: Prompt Chaining engine ready.")

    def _parse_json_safe(self, text: str) -> Any:
        if not text:
            return None
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            if text.lower() == "true": return True
            if text.lower() == "false": return False
            logger.debug(f"JSON Parse Error: {e} - Raw string: {text[:100]}")
            return None

    async def _generate_tool_arguments(self, endpoint: str, model: str, last_user_message: str, assistant_content: str, tool_name: str, tool_schema: dict) -> dict:
        prompt = f"""
あなたはAIアシスタントの内部システムとして機能する、ツール引数生成エンジンです。
以下の「ユーザーの発言」と「アシスタントの返答意図」を読み取り、対象ツールを実行するための正しいJSON引数を生成してください。

[ユーザーの発言]
{last_user_message}

[アシスタントの返答意図]
{assistant_content.strip()}

[対象ツール]
名前: {tool_name}
引数スキーマ (JSON Schema):
{json.dumps(tool_schema.get("parameters", {}), ensure_ascii=False, indent=2)}

【重要な指示】
- スキーマの要件（プロパティ名、型、必須項目）を厳密に満たすJSONオブジェクトを生成してください。
- アシスタントの返答意図から、ツールに必要な情報を抽出して引数に割り当ててください。
- markdownの装飾(```jsonなど)や説明文、挨拶などは一切書かず、純粋なJSON文字列だけを出力してください。
"""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8192,
            "temperature": 0.0,
            "stream": False
        }
        
        args = {}
        try:
            resp = await self.http_client.post(f"{endpoint}/chat/completions", json=payload, timeout=self.settings.llm.timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                parsed = self._parse_json_safe(content)
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    logger.error(f"No valid JSON object found in args generation.\nContent was: {content}")
            else:
                logger.error(f"HTTP Error in args generation: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Error generating tool args: {e}")
            
        reqs = tool_schema.get("parameters", {}).get("required", [])
        props = tool_schema.get("parameters", {}).get("properties", {})
        
        missing_reqs = [r for r in reqs if r not in args]
        if missing_reqs:
            logger.warning(f"Missing required parameters {missing_reqs}. Using fallback mapping.")
            for req in missing_reqs:
                req_lower = req.lower()
                if any(k in req_lower for k in ["question", "ask", "result", "summary", "message", "text", "content", "response", "command", "query"]):
                    args[req] = assistant_content.strip()
                else:
                    ptype = props.get(req, {}).get("type", "string")
                    if ptype == "array": args[req] = []
                    elif ptype == "object": args[req] = {}
                    elif ptype == "boolean": args[req] = False
                    elif ptype in ["number", "integer"]: args[req] = 0
                    else: args[req] = "undefined"

        return args

    async def process_workflow(self, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        import time
        endpoint = self.settings.llm.interlocutor_endpoint
        model_name = request.model
        
        launcher_client_name = self.settings.llm.launcher_client
        launcher_client = self.mcp_clients.get(launcher_client_name) if launcher_client_name else None

        if launcher_client and launcher_client.session:
            try:
                # 1. 現在稼働中のサーバーを取得して確認
                running_str = await launcher_client.call_tool("list_running_servers", {})
                running_servers = self._parse_json_safe(running_str)
                if not isinstance(running_servers, list):
                    running_servers = []
                
                is_running = any(
                    isinstance(s, dict) and (s.get("model") == model_name or s.get("modelId") == model_name)
                    for s in running_servers
                )
                
                if not is_running:
                    # 2. モデルが存在するか検索で確認
                    search_str = await launcher_client.call_tool("search_mlx_models", {"search_query": model_name, "limit": 10})
                    search_results = self._parse_json_safe(search_str)
                    if not isinstance(search_results, list):
                        search_results = []
                    
                    target_info = next((m for m in search_results if isinstance(m, dict) and (m.get("modelId") == model_name or m.get("id") == model_name)), None)
                    
                    if not target_info and search_results:
                        # [要件1] クライアントから指定されたモデルが存在しない場合
                        yield TextDeltaEvent(content=f"エラー: 指定されたモデル '{model_name}' はHugging Face上に存在しないか、MLXフォーマットではありません。正しいモデル名(ID)を指定してください。")
                        yield WorkflowFinishEvent(finish_reason="stop")
                        return
                    
                    is_cached = False
                    if target_info:
                        is_cached = target_info.get("cached", False) or target_info.get("is_cached", False) or target_info.get("downloaded", False)
                    
                    if not is_cached and target_info:
                        # [要件2] モデルは存在するが未ダウンロードの場合
                        yield TextDeltaEvent(content=f"モデル '{model_name}' は未ダウンロードです。これからダウンロードを開始します...\n")
                        try:
                            await launcher_client.call_tool("download_model", {"model_name": model_name})
                            yield TextDeltaEvent(content=f"\nモデル '{model_name}' のダウンロードが完了しました！起動準備をします...\n")
                        except Exception as e:
                            logger.warning(f"Download model tool returned error or warning: {e}")
                            yield TextDeltaEvent(content=f"\nダウンロード中に警告が発生しましたが、起動を試みます...\n")
                        
                    # [要件3] モデルがローカルに存在する場合 (起動する)
                    port = urlparse(endpoint).port or 8080
                    logger.info(f"Launching LLM Server for model: {model_name} on port {port}")
                    try:
                        await launcher_client.call_tool("launch_llm_server", {"port": port, "model_name": model_name})
                    except Exception as e_launch:
                        logger.debug(f"launch_llm_server failed ({e_launch}), trying restart_llm_server...")
                        await launcher_client.call_tool("restart_llm_server", {"port": port, "model_name": model_name})
                        
                    self.current_loaded_model = model_name
                    await asyncio.sleep(5)  # 起動完了バッファ
                else:
                    self.current_loaded_model = model_name
                    
            except Exception as e:
                logger.error(f"Failed to check/launch model via MCP tool: {e}")
                # 万が一ツール呼び出しに失敗した場合もフォールバックとして推論フローの続行を試みる
        else:
            logger.warning(f"Launcher client '{launcher_client_name}' is not available. Skipping pre-flight checks.")

        # --- 以降、通常のワークフロー（LLMからのストリーミング推論） ---
        json_payload = request.model_dump(exclude_none=True)
        max_retries = self.settings.agent.max_retries
        
        for attempt in range(max_retries + 1):
            try:
                full_content = ""
                has_tool_calls = False
                final_finish_reason = "stop"
                
                async for chunk in self.llm_client.stream_chat(endpoint, json_payload, self.settings.llm.timeout_sec):
                    if chunk.content:
                        full_content += chunk.content
                        yield TextDeltaEvent(content=chunk.content)
                    
                    if chunk.tool_calls:
                        has_tool_calls = True
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

                if request.tools and not has_tool_calls:
                    available_tools_dict = {
                        t.function.get("name"): t.function
                        for t in request.tools
                        if t.function and t.function.get("name")
                    }
                    if available_tools_dict:
                        available_tool_names = list(available_tools_dict.keys())
                        docs = [
                            f"Name: {tn}. Description: {available_tools_dict[tn].get('description', 'No description provided')}"
                            for tn in available_tool_names
                        ]
                        
                        selected_tool = available_tool_names[0]
                        reranker_client = self.mcp_clients.get("mcp-reranker")
                        
                        last_user_message = ""
                        if request.messages:
                            for msg in reversed(request.messages):
                                if msg.role == "user":
                                    last_user_message = msg.content or ""
                                    break
                        
                        query = (
                            f"ユーザーの発言: 「{last_user_message}」\n"
                            f"AIの返答: 「{full_content.strip()}」\n"
                            "この返答をクライアントに返す際に使用すべき、最も適切なツールをリストから1つ選択してください。"
                        )
                        
                        if reranker_client and reranker_client.session:
                            try:
                                result_str = await reranker_client.call_tool(
                                    "rerank_documents", 
                                    {"query": query, "documents": docs}
                                )
                                results = self._parse_json_safe(result_str)
                                if isinstance(results, list) and results:
                                    best_doc = results[0].get("document")
                                    for idx, doc_text in enumerate(docs):
                                        if doc_text == best_doc:
                                            selected_tool = available_tool_names[idx]
                                            break
                            except Exception as e:
                                logger.error(f"Reranker error: {e}")

                        tool_schema = available_tools_dict[selected_tool]
                        args = await self._generate_tool_arguments(
                            endpoint=endpoint,
                            model=model_name,
                            last_user_message=last_user_message,
                            assistant_content=full_content,
                            tool_name=selected_tool,
                            tool_schema=tool_schema
                        )
                        
                        self._log_to_gateway(request, query, request.tools, selected_tool, args)
                        
                        tc_id = f"call_rerank_{int(time.time())}"
                        yield SystemToolCallEvent(index=0, id=tc_id, tool_name=selected_tool, arguments=args)
                        final_finish_reason = "tool_calls"

                yield WorkflowFinishEvent(finish_reason=final_finish_reason)
                break
            
            except Exception as e:
                error_msg = str(e).lower()
                if "connect" in error_msg or "timeout" in error_msg:
                    if attempt < max_retries:
                        logger.warning(f"LLM connection error: {e}. Relaunching server via MCP tool...")
                        if launcher_client and launcher_client.session:
                            port = urlparse(endpoint).port or 8080
                            try:
                                await launcher_client.call_tool("launch_llm_server", {"port": port, "model_name": model_name})
                            except Exception:
                                try:
                                    await launcher_client.call_tool("restart_llm_server", {"port": port, "model_name": model_name})
                                except Exception as e_restart:
                                    logger.error(f"Restart failed: {e_restart}")
                        await asyncio.sleep(20)
                        continue
                
                logger.error(f"Streaming error: {e}")
                yield ErrorEvent(message=str(e))
                break

    async def shutdown(self):
        for client in self.mcp_clients.values():
            await client.stop()
        await self.http_client.aclose()