import os
import time
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from src.core.orchestrator import Orchestrator

app = FastAPI(title="Brownie OpenAI-Compatible API")

# グローバルな Orchestrator インスタンス (実際には main.py 等で初期化される)
orchestrator: Optional[Orchestrator] = None

# --- OpenAI 規格のデータモデル ---

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "brownie-v2"
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = 0.7

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"

class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{int(time.time())}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "brownie-v2"
    choices: List[ChatCompletionResponseChoice]

# --- エンドポイント ---

@app.on_event("startup")
async def startup_event():
    global orchestrator
    config_path = os.getenv("BROWNIE_CONFIG", "config/config.yaml")
    logger.info(f"Initializing Brownie Engine for API Server (Config: {config_path})")
    orchestrator = Orchestrator(config_path)
    # エンジンを非同期でバックグラウンド起動 (MCPマネージャー等のライフサイクル開始)
    import asyncio
    asyncio.create_task(orchestrator.start())

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    logger.info(f"Received completion request for model: {request.model}")
    
    # Orchestrator へタスクを投入 (OpenAI 互換メッセージをそのまま渡す)
    messages_dict = [m.dict() for m in request.messages]
    result = await orchestrator.submit_chat_completion(messages_dict, stream=request.stream)
    
    # 応答の組み立て (自律修正の結果をテキストとして返却)
    # note: result の構造はワークフローの出力に依存。ここでは文字列化して返却。
    content = str(result.get("output", "Task completed without specific output."))
    
    return ChatCompletionResponse(
        choices=[
            ChatCompletionResponseChoice(
                index=0,
                message=ChatMessage(role="assistant", content=content)
            )
        ]
    )

@app.get("/health")
async def health():
    return {"status": "ok", "engine_running": orchestrator.is_running if orchestrator else False}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
