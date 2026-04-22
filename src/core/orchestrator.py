import asyncio
import os
from pathlib import Path
from typing import Any, List, Dict

import httpx
from loguru import logger

from src.core.config import get_settings

class Orchestrator:
    """
    BROWNIE の最小コア・オーケストレーター。
    システムプロンプトの注入と LLM との対話に特化する。
    """

    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.system_prompt_path = self.project_root / ".brwn" / "system_prompt.md"
        
        # システムプロンプトの初期ロード
        self.system_prompt = self._load_system_prompt()
        
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
        """システムプロンプトを注入して LLM と対話する"""
        endpoint = self.settings.llm.orchestrator_endpoint
        model_name = self.settings.llm.models.get("orchestrator", "default")

        # システムプロンプトをメッセージの先頭に注入
        full_messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # 既存のメッセージからシステムプロンプトを除外して追加（二重注入防止）
        for msg in messages:
            if msg.get("role") != "system":
                full_messages.append(msg)

        payload = {
            "model": model_name,
            "messages": full_messages,
            "stream": stream
        }

        try:
            resp = await self.http_client.post(
                f"{endpoint}/chat/completions",
                json=payload
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"LLM Error: {resp.status_code} - {resp.text}")
                return self._error_response(f"LLM Error: {resp.status_code}")
        except Exception as e:
            logger.error(f"Connection Error: {e}")
            return self._error_response(f"Connection Error: {e}")

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
