import os
import yaml
import json
import asyncio
import logging
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

# --- Config & Settings (Step 1 統合) ---
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

def get_settings(config_path: str = "config/config.yaml") -> Settings:
    return Settings.load(config_path)

# --- Router (Step 2 統合) ---
class Router:
    """
    BROWNIE Router: 入力プロンプトに対して最適な担当モデルを選択する。
    LLMによるゼロショット分類（およびヒューリスティック）を使用し、
    巨大な機械学習モデル（torch等）への依存を排除した超軽量版。
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.endpoint = settings.llm.interlocutor_endpoint
        self.model_name = settings.llm.models.get("interlocutor", "default")
        self.timeout = settings.llm.timeout_sec
        
        # 高速判定用のキーワード（これらが含まれていれば LLM に聞かず即 Coder へ）
        self.coder_keywords = [
            "コード", "修正", "実装", "バグ", "エラー", "リファクタ",
            "スクリプト", "ファイル", "プログラム", "作って", "追加して"
        ]
        logger.info("Lightweight LLM Router initialized.")

    async def route(self, query: str) -> str:
        """
        クエリに対して最適なモデルラベル('coder' または 'interlocutor')を返す。
        """
        if not query:
            return "interlocutor"

        # 1. ヒューリスティック (高速・一次判定)
        for kw in self.coder_keywords:
            if kw in query:
                logger.debug(f"Router: Keyword match '{kw}' -> coder")
                return "coder"

        # 2. LLM によるゼロショット判定 (フォールバック)
        logger.debug("Router: Falling back to LLM classification...")
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
                
                if "coder" in answer:
                    logger.debug("Router: LLM decided -> coder")
                    return "coder"
                else:
                    logger.debug("Router: LLM decided -> interlocutor")
                    return "interlocutor"
        except Exception as e:
            logger.error(f"Router LLM Error: {e}. Defaulting to interlocutor.")
            return "interlocutor"

# --- Gateway Client (Step 3 統合) ---
class GatewayClient:
    """
    Brownie から MCP Routing Gateway に接続し、ツール一覧の取得や実行を行うクライアント。
    mcp-routing-gateway の BackendClient をベースに、単一接続に最適化。
    """
    def __init__(self, command: str = "mcp-gateway", args: Optional[List[str]] = None):
        # 起動するゲートウェイコマンド (必要に応じて config.yaml のパスなどを args に渡す)
        self.command = command
        self.args = args or []
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()

    async def start(self):
        """ゲートウェイプロセス(stdio)を起動し、セッションを確立する"""
        try:
            # 環境変数をそのまま引き継いで実行
            server_params = StdioServerParameters(
                command=self.command, 
                args=self.args, 
                env=os.environ.copy()
            )
            
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            read_stream, write_stream = stdio_transport
            
            self.session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            
            # 初期化ハンドシェイク
            await self.session.initialize()
            
            logger.info("✅ Successfully connected to MCP Routing Gateway.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Gateway: {e}")
            raise

    async def stop(self):
        """ゲートウェイプロセスを安全に終了させる"""
        await self._exit_stack.aclose()
        self.session = None
        logger.info("Gateway connection closed.")

    async def fetch_tools(self) -> List[Dict[str, Any]]:
        """ゲートウェイが Pydantic で生成した安全な仮想ツール一覧を取得する"""
        if not self.session:
            logger.error("Gateway is not connected.")
            return []
        
        try:
            tools_result = await self.session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema
                }
                for t in tools_result.tools
            ]
        except Exception as e:
            logger.error(f"Failed to fetch tools from Gateway: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """LLMからのツール実行要求をゲートウェイへ送信する"""
        if not self.session:
            raise ValueError("Gateway is not connected.")
        
        logger.info(f"Calling virtual tool '{tool_name}' via Gateway")
        result = await self.session.call_tool(tool_name, arguments)
        
        # MCP の結果 (TextContent等) を LLM が読みやすい単一の文字列にパース
        output = ""
        for content in result.content:
            if isinstance(content, types.TextContent):
                output += content.text + "\n"
            else:
                # 画像などテキスト以外のコンテンツの場合のフォールバック
                output += f"[{content.type} content]\n"
                
        return output.strip()

# --- Core Tools & Orchestrator ---
class MCPVirtualTool(Tool):
    """
    MCP Gateway から取得したツール定義を smolagents 形式に動的変換するラッパー。
    """
    def __init__(self, mcp_tool_def, mcp_client, loop):
        self.name = mcp_tool_def["name"]
        self.description = mcp_tool_def["description"]
        
        props = mcp_tool_def.get("inputSchema", {}).get("properties", {})
        self.inputs = {}
        for k, v in props.items():
            self.inputs[k] = {
                "type": v.get("type", "string"),
                "description": v.get("description", "")
            }
            
        self.output_type = "string"
        self.mcp_client = mcp_client
        self._loop = loop
        self.is_initialized = True
        self.skip_forward_signature_validation = True
        super().__init__()

    def forward(self, **kwargs):
        future = asyncio.run_coroutine_threadsafe(
            self.mcp_client.call_tool(self.name, kwargs),
            self._loop
        )
        return future.result()


class Orchestrator:
    """
    BROWNIE オーケストレーター
    - マクロ制御: YAMLワークフローによる手順とツール制約の強制
    - ミクロ自律: smolagents SDK による ToolCalling ハンドリング
    """
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.workflows_dir = self.project_root / "workflows"
        self.system_prompt_path = self.project_root / ".brwn" / "system_prompt.md"
        self.mcp_config_path = self.project_root / "mcp_config.json"
        
        self.system_prompt = self._load_system_prompt()
        self.router = Router(settings=self.settings)
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        
        # --- mcp_config.json から Gateway の起動設定を動的に読み込む ---
        gateway_cmd = "mcp-gateway"
        gateway_args = []
        
        if self.mcp_config_path.exists():
            try:
                with open(self.mcp_config_path, "r", encoding="utf-8") as f:
                    mcp_config = json.load(f)
                    servers = mcp_config.get("mcpServers", {})
                    if "mcp-routing-gateway" in servers:
                        gw_conf = servers["mcp-routing-gateway"]
                        gateway_cmd = gw_conf.get("command", gateway_cmd)
                        gateway_args = gw_conf.get("args", gateway_args)
            except Exception as e:
                logger.error(f"Failed to load mcp_config.json: {e}")

        # 環境変数によるオーバーライド（テスト時などに使用）
        gateway_cmd = os.getenv("BROWNIE_GATEWAY_CMD", gateway_cmd)
        
        self.mcp_client = GatewayClient(
            command=gateway_cmd,
            args=gateway_args
        )

    async def start(self):
        await self.mcp_client.start()
        logger.info("✅ Orchestrator: Hybrid-Workflow engine ready.")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            try:
                return self.system_prompt_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to read system prompt: {e}")
                return "You are BROWNIE."
        return "You are BROWNIE."

    async def submit_chat_completion(self, messages: List[Dict[str, str]], stream: bool = False):
        return await self.orchestrate(messages, stream=stream)

    async def orchestrate(self, messages: List[Dict[str, str]], stream: bool = False):
        current_context = messages.copy()
        user_input = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        actor = await self.router.route(user_input)
        logger.info(f"Selected Actor: {actor}")

        return await self._run_workflow(actor, current_context, stream)

    async def _run_workflow(self, actor: str, current_context: List[Dict[str, str]], stream: bool):
        workflow_path = self.workflows_dir / f"{actor}.yaml"
        if not workflow_path.exists():
            return self._error_response(f"Workflow not found: {actor}")

        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                workflow = yaml.safe_load(f)
        except Exception as e:
            return self._error_response(f"YAML Load Error: {e}")

        mcp_tools = await self.mcp_client.fetch_tools()
        loop = asyncio.get_running_loop()
        final_result = ""
        steps = workflow.get("steps", [])

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                return self._error_response(f"Step {i+1} is missing required 'model_key'.")

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            model_name = self.settings.llm.models.get(model_key, "default")

            step_type = step.get("type")

            if step_type == "llm_chat":
                return await self._call_llm(model_key, endpoint, current_context, stream)

            elif step_type == "agent_task":
                description = step.get("description", "")
                allowed = step.get("allowed_tools", [])
                
                logger.info(f"Step {i+1}: Executing with {model_key}")
                
                history = "\n".join([f"{m['role']}: {m['content']}" for m in current_context])
                instruction = f"{self.system_prompt}\n\n[History]\n{history}\n\n[Task]\n{description}"

                agent_model = OpenAIServerModel(
                    model_id=model_name,
                    api_base=endpoint,
                    api_key="none"
                )
                
                step_tools = [
                    MCPVirtualTool(t, self.mcp_client, loop) 
                    for t in mcp_tools if not allowed or t["name"] in allowed
                ]

                agent = ToolCallingAgent(tools=step_tools, model=agent_model, max_steps=10)
                
                try:
                    result = await asyncio.to_thread(agent.run, instruction)
                    final_result = str(result)
                    current_context.append({"role": "assistant", "content": f"[Result Step {i+1}]\n{final_result}"})
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

    async def _call_llm(self, model_key: str, endpoint: str, messages: List[Dict[str, str]], stream: bool):
        model_name = self.settings.llm.models.get(model_key, "default")
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": f"{self.system_prompt}\n\n{m['content']}"} if i == 0 else m for i, m in enumerate(messages)],
            "stream": stream
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                resp = await client.post(f"{endpoint}/chat/completions", json=payload)
                return resp.json() if resp.status_code == 200 else self._error_response(f"LLM Error: {resp.status_code}")
        except Exception as e:
            return self._error_response(f"Conn Error: {e}")

    def _error_response(self, message: str) -> Dict[str, Any]:
        return {"choices": [{"message": {"role": "assistant", "content": f"ERROR: {message}"}, "finish_reason": "error"}]}

    async def shutdown(self):
        await self.mcp_client.stop()
        await self.http_client.aclose()