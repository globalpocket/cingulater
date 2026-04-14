import asyncio
import json
import httpx
from pydantic import BaseModel
from pydantic_ai import Agent
from src.llm.robust_model import get_robust_model

# モックレスポンスの定義（id, usage, choices.index が欠落）
MOCK_RESPONSE = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"draft_comment": "Final verification successful"}'
            },
            "finish_reason": "stop"
        }
    ]
}

class MockResponseTransport(httpx.BaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        print(f"DEBUG: Intercepted request to {request.url}")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(MOCK_RESPONSE).encode()
        )

class TestOutput(BaseModel):
    draft_comment: str

async def test_robustness():
    print("--- Final Testing of RobustOpenAIModel ---")
    
    # 堅牢なモデルの取得
    model = get_robust_model("test-model", base_url="http://localhost:9999")
    
    # 内部の OpenAIProvider -> httpx.AsyncClient のトランスポートをモックに差し替え
    # OpenAIModel -> provider (OpenAIProvider) -> _client (AsyncOpenAI) -> _client (httpx.AsyncClient)
    # または OpenAIProvider._client._client
    model.provider.client._client._transport = MockResponseTransport()
    
    agent = Agent(model, output_type=TestOutput)
    
    try:
        result = await agent.run("Hello")
        print(f"Result successful: {result.data.draft_comment}")
        return True
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_robustness())
    if success:
        print("\n✅ Robustness layer verified with official Provider pattern!")
    else:
        print("\n❌ Robustness layer failed.")
