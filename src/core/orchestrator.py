import os
import yaml
import json
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel

from core.config import get_settings
from core.router import Router
from gateway.client import GatewayClient


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
        # 動的な引数(kwargs)を受け入れるために、smolagents のシグネチャ検証をスキップ
        self.skip_forward_signature_validation = True
        super().__init__()

    def forward(self, **kwargs):
        # smolagents(同期)から非同期の mcp_client を安全に呼び出す
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
        
        self.system_prompt = self._load_system_prompt()
        self.router = Router(settings=self.settings)
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        
        gateway_cmd = os.getenv("BROWNIE_GATEWAY_CMD", "mcp-gateway")
        self.mcp_client = GatewayClient(
            command=gateway_cmd,
            args=[
                "--work-dir", str(self.project_root),
                "--config", "gateway_config.json",
                "--mcp-config", "gateway_mcp_config.json"
            ]
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
            # model_key の明示的な指定を必須とする
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

                # OpenAIServerModel は内部で openai SDK を使用
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
                    # smolagents の実行(同期)を別スレッドに逃がす
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