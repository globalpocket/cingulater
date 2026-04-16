import asyncio
import json
from loguru import logger
import re
import time
from typing import Optional
from tenacity import AsyncRetrying, stop_after_delay, wait_exponential, retry_if_exception_type

import httpx
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger("brownie.llm_utils")

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
                    "total_tokens": 0
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
                            r"call:([a-zA-Z0-9_]+)([\{\(].*[\}\)])",
                            content,
                            re.DOTALL
                        )
                        if match:
                            tool_name = match.group(1)
                            tool_args_str = match.group(2).strip()
                            if "<|\\\"|>" in tool_args_str:
                                tool_args_str = tool_args_str.replace(
                                "<|\\\"|>", "\\\""
                            )
                            if "<|\">" in tool_args_str:
                                tool_args_str = tool_args_str.replace("<|\">", "\"")
                            tool_args_str = re.sub(
                                r'([\{\s,])([a-zA-Z0-9_]+):',
                                r'\1"\2":',
                                tool_args_str
                            )
                            
                            tool_call_id = "call_" + data.get("id", "placeholder")[-8:]
                            tool_call = {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_args_str
                                }
                            }
                            has_no_tools = (
                                not message.get("tool_calls") or
                                not isinstance(message["tool_calls"], list)
                            )
                            if has_no_tools:
                                message["tool_calls"] = []
                            message["tool_calls"].append(tool_call)
                            message["content"] = "" 
                            modified = True
                            logger.info(
                                f"Converted and healed tool call '{tool_name}'"
                            )

            if modified:
                response._content = json.dumps(data).encode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to apply robustness fixes to LLM response: {e}")

async def wait_for_llm_ready(endpoint: str, timeout: int = 180):
    if not endpoint:
        return True
    if "localhost" in endpoint:
        endpoint = endpoint.replace("localhost", "127.0.0.1")
    url = f"{endpoint.rstrip('/')}/models"
    logger.info(f"Waiting for LLM server at {url} (timeout: {timeout}s)...")
    
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_delay(timeout),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)),
            ):
                with attempt:
                    resp = await client.get(url, timeout=2.0)
                    if resp.status_code == 200:
                        logger.info(f"LLM server at {endpoint} is READY.")
                        return True
                    # 200以外もリトライ対象にする（起動直後のエラー等）
                    raise httpx.ReadTimeout(f"Server returned {resp.status_code}")
        except Exception as e:
            logger.error(f"LLM server at {endpoint} failed to become ready: {e}")
            return False
    return False

def get_robust_model(model_name: str, base_url: Optional[str] = None) -> OpenAIModel:
    if base_url and "localhost" in base_url:
        base_url = base_url.replace("localhost", "127.0.0.1")
    http_client = httpx.AsyncClient(
        event_hooks={"response": [robust_response_hook]},
        timeout=httpx.Timeout(120.0, connect=10.0),
        trust_env=False
    )
    provider = OpenAIProvider(
        base_url=base_url,
        api_key="EMPTY",
        http_client=http_client
    )
    return OpenAIModel(model_name, provider=provider)
