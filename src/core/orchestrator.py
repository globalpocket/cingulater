from pathlib import Path
from typing import Any, List, Dict

import httpx
from loguru import logger

from src.core.config import get_settings
from src.core.router import Router

class Orchestrator:
    """
    BROWNIE の中央集権的オーケストレーター。
    Router Pattern を用い、適切なモデルへ処理を振り分ける。
    """

    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.system_prompt_path = self.project_root / ".brwn" / "system_prompt.md"
        
        # システムプロンプトの初期ロード
        self.system_prompt = self._load_system_prompt()
        
        # Router の初期化
        self.router = Router(model_name=self.settings.llm.models.get("router"))
        
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.is_running = True

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
        中央集権的ルーティングループ。
        Router の判断に基づき、Interlocutor または Coder を呼び出す。
        """
        current_context = messages.copy()
        loop_count = 0
        max_loops = self.settings.llm.router.max_routing_loops

        # 最新のユーザー入力を取得
        user_input = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break

        while loop_count < max_loops:
            logger.info(f"Routing Loop #{loop_count + 1}...")
            
            # 1. Router による判定
            actor = self.router.route(user_input)

            if actor == "interlocutor":
                logger.info("Executing Interlocutor...")
                return await self._call_llm(
                    "interlocutor", 
                    self.settings.llm.interlocutor_endpoint, 
                    current_context, 
                    stream
                )

            elif actor == "coder":
                logger.info("Executing Coder...")
                coder_resp = await self._call_llm(
                    "coder", 
                    self.settings.llm.coder_endpoint, 
                    current_context, 
                    stream
                )
                
                # Coder の結果を取得
                result_content = coder_resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                if not result_content:
                    logger.warning("Coder returned empty response. Terminating loop to prevent context corruption.")
                    result_content = "(Coder did not generate any output, but task may have been completed via Tool Calls.)"

                current_context.append({"role": "assistant", "content": result_content})
                user_input = f"Coder 処理完了。以下の結果をユーザーに報告してください: {result_content}"
                
                loop_count += 1
                continue

        return self._error_response("Max routing loops exceeded.")

    async def _call_llm(self, model_key: str, endpoint: str, messages: List[Dict[str, str]], stream: bool):
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

        # max_tokens: 4096 を追加（途切れるバグを修正）
        payload = {
            "model": model_name,
            "messages": full_messages,
            "stream": stream,
            "max_tokens": 4096
        }

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
        self.is_running = False
        await self.http_client.aclose()
