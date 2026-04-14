import json
import logging
import httpx
from typing import Optional, Any
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

async def robust_response_hook(response: httpx.Response):
    """
    HTTP レスポンスをインターセプトし、規格不備（欠落フィールド）を補完する。
    Local LLM サーバー（MLX 等）が OpenAI 規格のメタデータを返さない場合のバリデーションエラーを防ぐ。
    """
    # 200 OK かつ JSON レスポンスの場合のみ処理
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and "application/json" in content_type:
        try:
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

            # 4. usage フィールドの補完
            if "usage" not in data:
                data["usage"] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
                modified = True

            # 5. model フィールドの補完
            if not data.get("model"):
                data["model"] = "robust-model-placeholder"
                modified = True

            if modified:
                logger.debug(f"Applied robustness fixes to LLM response: {data.get('id')}")
                # 修正した JSON でレスポンス内容を書き換える
                response._content = json.dumps(data).encode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to apply robustness fixes to LLM response: {e}")

def get_robust_model(model_name: str, base_url: Optional[str] = None) -> OpenAIModel:
    """
    ローカルサーバーの非標準的な挙動を吸収する設定済みの OpenAIModel を提供する
    """
    # カスタムフックを設定した HTTP クライアントを作成
    http_client = httpx.AsyncClient(
        event_hooks={"response": [robust_response_hook]},
        timeout=httpx.Timeout(120.0, connect=10.0)
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
