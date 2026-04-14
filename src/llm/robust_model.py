import json
import logging
import httpx
import os
from typing import Optional, Any
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

async def robust_response_hook(response: httpx.Response):
    """
    HTTP レスポンスをインターセプトし、規格不備（欠落フィールド）を補完する。
    Local LLM サーバー（MLX 等）が OpenAI 規格のメタデータを返さない場合のバリデーションエラーを防ぐ。
    """
    print(f"DEBUG: robust_response_hook called for {response.url}")
    # 200 OK かつ JSON レスポンスの場合のみ処理
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and "application/json" in content_type:
        try:
            # コンテンツを読み込む
            await response.aread()
            data = response.json()
            modified = False

            # 1. 必須メタデータ id の補完
            if not data.get("id"):
                data["id"] = "chatcmpl-robust-placeholder"
                modified = True

            # 2. object フィールドの補完
            if not data.get("object"):
                data["object"] = "chat.completion"
                modified = True

            # 3. choices フィールド内の index 補完
            if "choices" in data and isinstance(data["choices"], list):
                for i, choice in enumerate(data["choices"]):
                    if "index" not in choice:
                        choice["index"] = i
                        modified = True

            # 4. usage フィールドの補完（深くチェック）
            usage = data.get("usage")
            if not isinstance(usage, dict):
                data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                modified = True
            else:
                for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                    if not isinstance(usage.get(key), int):
                        usage[key] = 0
                        modified = True

            # 5. model フィールドの補完
            if not data.get("model"):
                data["model"] = "robust-model-placeholder"
                modified = True

            # 6. created フィールドの補完
            if not data.get("created"):
                import time
                data["created"] = int(time.time())
                modified = True

            # 7. テキストベースのツール呼び出しを構造化データへ変換 (Gemma-4 / MLX 対処)
            if "choices" in data and isinstance(data["choices"], list):
                for choice in data["choices"]:
                    message = choice.get("message", {})
                    content = message.get("content", "")
                    if content and "<|tool_call" in content:
                        import re
                        # よりアグレッシブな検索: call:name 後のブロックを全て取得
                        match = re.search(r"call:([a-zA-Z0-9_]+)([\{\(].*[\}\)])", content, re.DOTALL)
                        if match:
                            tool_name = match.group(1)
                            tool_args_str = match.group(2).strip()
                            # JSON ヒーリング
                            if "<|\\\"|>" in tool_args_str:
                                tool_args_str = tool_args_str.replace("<|\\\"|>", "\\\"")
                            if "<|\">" in tool_args_str:
                                tool_args_str = tool_args_str.replace("<|\">", "\"")
                            tool_args_str = re.sub(r'([\{\s,])([a-zA-Z0-9_]+):', r'\1"\2":', tool_args_str)
                            
                            tool_call_id = "call_" + data.get("id", "placeholder")[-8:]
                            tool_call = {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_args_str
                                }
                            }
                            if not message.get("tool_calls") or not isinstance(message["tool_calls"], list):
                                message["tool_calls"] = []
                            message["tool_calls"].append(tool_call)
                            message["content"] = "" 
                            modified = True
                            logger.info(f"Converted and healed tool call '{tool_name}' for local LLM.")

            if modified:
                # 修正した JSON でレスポンス内容を書き換える
                response._content = json.dumps(data).encode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to apply robustness fixes to LLM response: {e}")

def get_robust_model(model_name: str, base_url: Optional[str] = None) -> OpenAIModel:
    """
    ローカルサーバーの非標準的な挙動を吸収する設定済みの OpenAIModel を提供する
    """
    # 接続先を 127.0.0.1 に固定して IPv6 競合やプロキシ問題を回避
    if base_url and "localhost" in base_url:
        base_url = base_url.replace("localhost", "127.0.0.1")
        
    logger.info(f"Creating robust model for {model_name} at {base_url}")
    
    # カスタムフックを設定した HTTP クライアントを作成
    http_client = httpx.AsyncClient(
        event_hooks={"response": [robust_response_hook]},
        timeout=httpx.Timeout(120.0, connect=10.0),
        trust_env=False # 環境変数のプロキシ設定を無視
    )
    
    # OpenAI Provider を作成し、カスタム HTTP クライアントを注入
    provider = OpenAIProvider(
        base_url=base_url,
        api_key="EMPTY",
        http_client=http_client
    )
    
    return OpenAIModel(
        model_name,
        provider=provider
    )
