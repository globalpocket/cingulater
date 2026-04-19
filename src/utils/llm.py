import json
import os
import re
import time
from typing import Optional

import httpx
import litellm
from loguru import logger
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

# tenacity imports removed since they were only used in dead code

# LiteLLM の基本設定
litellm.telemetry = False
litellm.drop_params = True  # 未対応パラメータを自動でドロップ


async def robust_response_hook(response: httpx.Response):
    """
    HTTP レスポンスをインターセプトし、規格不備（欠落フィールド）を補完する。
    Local LLM サーバー（MLX 等）が OpenAI 規格のメタデータを返さない場合の
    バリデーションエラーを防ぐ。
    """
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and "application/json" in content_type:
        try:
            await response.aread()
            data = response.json()
            modified = False

            if not data.get("id"):
                data["id"] = "chatcmpl-robust-placeholder"
                modified = True

            if not data.get("object"):
                data["object"] = "chat.completion"
                modified = True

            if "choices" in data and isinstance(data["choices"], list):
                for i, choice in enumerate(data["choices"]):
                    if "index" not in choice:
                        choice["index"] = i
                        modified = True

            usage = data.get("usage")
            if not isinstance(usage, dict):
                data["usage"] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
                modified = True
            else:
                for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                    if not isinstance(usage.get(key), int):
                        usage[key] = 0
                        modified = True

            if not data.get("model"):
                data["model"] = "robust-model-placeholder"
                modified = True

            if not data.get("created"):
                data["created"] = int(time.time())
                modified = True

            # テキストベースのツール呼び出しを構造化データへ変換 (Gemma-4 / MLX 対処)
            if "choices" in data and isinstance(data["choices"], list):
                for choice in data["choices"]:
                    message = choice.get("message", {})
                    content = message.get("content", "")
                    if content and "<|tool_call" in content:
                        match = re.search(
                            r"call:([a-zA-Z0-9_]+)([\{\(].*[\}\)])", content, re.DOTALL
                        )
                        if match:
                            tool_name = match.group(1)
                            tool_args_str = match.group(2).strip()
                            if '<|\\"|>' in tool_args_str:
                                tool_args_str = tool_args_str.replace('<|\\"|>', '\\"')
                            if '<|">' in tool_args_str:
                                tool_args_str = tool_args_str.replace('<|">', '"')
                            tool_args_str = re.sub(
                                r"([\{\s,])([a-zA-Z0-9_]+):", r'\1"\2":', tool_args_str
                            )

                            tool_call_id = "call_" + data.get("id", "placeholder")[-8:]
                            tool_call = {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_args_str,
                                },
                            }
                            has_no_tools = not message.get(
                                "tool_calls"
                            ) or not isinstance(message["tool_calls"], list)
                            if has_no_tools:
                                message["tool_calls"] = []
                            message["tool_calls"].append(tool_call)
                            message["content"] = ""
                            modified = True
                            logger.info(f"Converted and healed tool call '{tool_name}'")

            if modified:
                response._content = json.dumps(data).encode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to apply robustness fixes to LLM response: {e}")


def get_robust_model(model_name: str, base_url: Optional[str] = None) -> OpenAIModel:
    """
    LiteLLM を介して抽象化された Pydantic AI 用モデルを取得する。
    接続先を LiteLLM (または直接のプロバイダ) に向ける。
    """
    if base_url and "localhost" in base_url:
        base_url = base_url.replace("localhost", "127.0.0.1")

    http_client = httpx.AsyncClient(
        event_hooks={"response": [robust_response_hook]},
        timeout=httpx.Timeout(120.0, connect=10.0),
        trust_env=False,
    )

    # LiteLLM が提供する OpenAI 互換レイヤーを利用する構成
    provider = OpenAIProvider(
        base_url=base_url,
        api_key=os.getenv("OPENAI_API_KEY", "EMPTY"),
        http_client=http_client,
    )

    return OpenAIModel(model_name, provider=provider)
