import re
import yaml
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import get_settings
from core.router import Router
from gateway.client import GatewayClient


class Orchestrator:
    """
    BROWNIE の中央集権的オーケストレーター。
    Router Pattern を用い、適切なモデルへ処理を振り分ける。
    GatewayClient を通じて MCP ゲートウェイと接続し、AI に安全なツールを提供する。
    """

    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.system_prompt_path = self.project_root / ".brwn" / "system_prompt.md"
        
        # システムプロンプトの初期ロード
        self.system_prompt = self._load_system_prompt()
        
        # Router の初期化 (Settings を渡す)
        self.router = Router(settings=self.settings)
        
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        
        # 本番運用想定: PATH が通っている前提。必要に応じて環境変数でパスを上書き可能
        gateway_cmd = os.getenv("BROWNIE_GATEWAY_CMD", "mcp-gateway")
        logger.debug(f"Using Gateway command: {gateway_cmd}")

        self.mcp_client = GatewayClient(
            command=gateway_cmd,
            args=[
                "--work-dir", str(self.project_root),
                "--config", "gateway_config.json",
                "--mcp-config", "gateway_mcp_config.json"
            ]
        )
        self.is_running = True

    async def start(self):
        """MCP ゲートウェイとの接続を開始する"""
        await self.mcp_client.start()
        logger.info("✅ Orchestrator: MCP Gateway Client started.")

    def _load_system_prompt(self) -> str:
        """.brwn/system_prompt.md を読み込む"""
        if self.system_prompt_path.exists():
            try:
                return self.system_prompt_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to read system prompt: {e}")
                return "You are BROWNIE, an AI assistant."
        else:
            logger.warning(f"System prompt file not found at {self.system_prompt_path}")
            return "You are BROWNIE, an AI assistant."

    async def submit_chat_completion(self, messages: List[Dict[str, str]], stream: bool = False):
        """外部（CLI/API）からの対話要求を受け付ける"""
        return await self.orchestrate(messages, stream=stream)

    async def orchestrate(self, messages: List[Dict[str, str]], stream: bool = False):
        """
        Router の判断に基づき、Interlocutor または Coder を呼び出す。
        Coderの場合は事前にYAMLで計画を立案させ、タスクランナーとして順次実行する。
        """
        current_context = messages.copy()

        # 最新のユーザー入力を取得
        user_input = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break

        # 1. Router による判定 (非同期化)
        actor = await self.router.route(user_input)
        logger.info(f"Selected Actor: {actor}")

        if actor == "interlocutor":
            logger.info("Executing Interlocutor...")
            return await self._call_llm(
                "interlocutor", 
                self.settings.llm.interlocutor_endpoint, 
                current_context, 
                stream
            )

        elif actor == "coder":
            # ==========================================
            # 1. 計画立案フェーズ (YAML出力強制)
            # ==========================================
            logger.info("Executing Planning Phase...")
            planning_prompt = (
                "直前の依頼を達成するための実装計画を、以下のYAMLフォーマットで出力してください。\n"
                "必ずYAMLコードブロック(```yaml ... ```)内に記述し、この段階では実際のコード修正やツール実行は絶対に行わないでください。\n\n"
                "```yaml\n"
                "plan:\n"
                "  - step: 1\n"
                "    description: \"対象ファイルの特定と内容の読み込み\"\n"
                "  - step: 2\n"
                "    description: \"XXXのロジックをYYYに変更する\"\n"
                "  - step: 3\n"
                "    description: \"テストまたは検証を実行する\"\n"
                "```"
            )
            planning_context = current_context + [{"role": "user", "content": planning_prompt}]
            
            plan_resp = await self._call_llm("coder", self.settings.llm.coder_endpoint, planning_context, stream=False)
            plan_content = plan_resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # ==========================================
            # 2. タスクリストのパース
            # ==========================================
            yaml_match = re.search(r"```yaml\s*(.*?)\s*```", plan_content, re.DOTALL)
            steps = []
            if yaml_match:
                try:
                    plan_data = yaml.safe_load(yaml_match.group(1))
                    steps = plan_data.get("plan", [])
                except yaml.YAMLError as e:
                    logger.error(f"Failed to parse YAML plan: {e}")
            
            if not steps:
                logger.error("LLM failed to output a valid YAML plan.")
                return self._error_response("システムエラー: 実行計画のパースに失敗しました。もう一度やり直してください。")

            logger.info(f"Generated Plan with {len(steps)} steps.")
            
            # 計画全体をユーザーへの返答として履歴に積んでおく
            current_context.append({"role": "assistant", "content": f"以下の手順で実行します。\n{plan_content}"})

            # ==========================================
            # 3. タスクランナーループ (MCPツール対応版)
            # ==========================================
            final_result = ""
            # ゲートウェイから利用可能な安全なツール一覧を取得
            available_tools = await self.mcp_client.fetch_tools()

            for step_info in steps:
                step_num = step_info.get("step")
                desc = step_info.get("description")
                
                logger.info(f"Runner: Executing Step {step_num}: {desc}")
                
                # Coderに「このステップだけを実行せよ」と制限をかけて指示
                step_prompt = (
                    f"【システム指示: Step {step_num} の実行】\n"
                    f"現在のタスク: {desc}\n\n"
                    f"必要に応じてツールを使用し、このステップを完了させてください。完了したら結果を報告してください。次のステップへは進まないでください。"
                )
                current_context.append({"role": "user", "content": step_prompt})
                
                # ツール実行ループ: AIが「完了」と判断するまで回す
                while True:
                    coder_resp = await self._call_llm(
                        "coder", 
                        self.settings.llm.coder_endpoint, 
                        current_context, 
                        stream=False,
                        tools=available_tools
                    )
                    
                    message = coder_resp.get("choices", [{}])[0].get("message", {})
                    current_context.append(message)
                    
                    tool_calls = message.get("tool_calls")
                    if not tool_calls:
                        # ツール呼び出しがなければ、このステップは完了
                        final_result = message.get("content", "")
                        break
                    
                    # 各ツール呼び出しを順次実行
                    for tool_call in tool_calls:
                        t_name = tool_call["function"]["name"]
                        try:
                            t_args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError:
                            t_args = {}
                        
                        logger.info(f"Executing tool: {t_name}")
                        try:
                            tool_result = await self.mcp_client.call_tool(t_name, t_args)
                        except Exception as e:
                            logger.error(f"Tool execution failed: {e}")
                            tool_result = f"Error executing tool {t_name}: {str(e)}"
                        
                        # 実行結果を履歴に追加してAIにフィードバック
                        current_context.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": t_name,
                            "content": tool_result
                        })

            # ==========================================
            # 4. 完了報告
            # ==========================================
            return {
                "choices": [{
                    "message": {
                        "role": "assistant", 
                        "content": f"すべての計画ステップ（全{len(steps)}工程）が完了しました。\n\n最終報告:\n{final_result}"
                    },
                    "finish_reason": "stop"
                }]
            }

    async def _call_llm(self, model_key: str, endpoint: str, messages: List[Dict[str, str]], stream: bool, tools: Optional[List[dict]] = None):
        """指定されたモデルエンドポイントを呼び出す"""
        model_name = self.settings.llm.models.get(model_key, "default")

        # システムプロンプトを注入（Gemma互換）
        full_messages = []
        system_prompt_applied = False
        
        for msg in messages:
            if msg.get("role") == "system":
                continue # 既存のsystemロールは無視
            
            # 最初のuserメッセージにシステムプロンプトを合体させる
            if msg.get("role") == "user" and not system_prompt_applied:
                full_messages.append({
                    "role": "user",
                    "content": f"{self.system_prompt}\n\n{msg.get('content')}"
                })
                system_prompt_applied = True
            else:
                full_messages.append(msg)

        payload = {
            "model": model_name,
            "messages": full_messages,
            "stream": stream,
            "max_tokens": 4096
        }
        
        if tools:
            payload["tools"] = tools

        try:
            logger.debug(f"Calling LLM ({model_key}) with {len(full_messages)} messages.")
            async with httpx.AsyncClient(timeout=self.settings.llm.timeout_sec) as client:
                resp = await client.post(
                    f"{endpoint}/chat/completions",
                    json=payload
                )
                if resp.status_code == 200:
                    result = resp.json()
                    logger.debug(f"LLM Response ({model_key}): {result}")
                    return result
                else:
                    logger.error(f"{model_key} Error: {resp.status_code} - {resp.text}")
                    return self._error_response(f"{model_key} Error: {resp.status_code}")
        except Exception as e:
            logger.error(f"{model_key} Connection Error: {e}")
            return self._error_response(f"{model_key} Connection Error: {e}")

    def _error_response(self, message: str) -> Dict[str, Any]:
        return {
            "choices": [{
                "message": {"role": "assistant", "content": f"ERROR: {message}"},
                "finish_reason": "error"
            }]
        }

    async def shutdown(self):
        """プロセスの安全な停止処理"""
        self.is_running = False
        await self.mcp_client.stop()
        await self.http_client.aclose()
        logger.info("Orchestrator: Shutdown complete.")