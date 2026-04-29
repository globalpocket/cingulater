import os
import time
from typing import List, Optional, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator

# グローバルな Orchestrator インスタンス
orchestrator: Optional[Orchestrator] = None

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "brownie-v2"
    messages: List[ChatMessage]
    stream: bool = False

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバーの起動・終了処理を管理するライフイベントハンドラ"""
    global orchestrator
    config_path = os.getenv("BROWNIE_CONFIG", "config.yaml")
    logger.info(f"Initializing Brownie Core (Config: {config_path})")
    
    # オーケストレーターの初期化
    orchestrator = Orchestrator(config_path)
    
    # MCP ゲートウェイとの接続を開始
    await orchestrator.start()
    logger.info("Brownie Engine is online and connected to MCP Gateway.")
    
    yield
    
    # サーバー終了時のクリーンアップ
    if orchestrator:
        logger.info("Shutting down Brownie Core...")
        # Orchestrator の終了メソッド (stop または shutdown) を呼び出し
        if hasattr(orchestrator, "stop"):
            await orchestrator.stop()
        elif hasattr(orchestrator, "shutdown"):
            await orchestrator.shutdown()

app = FastAPI(title="Brownie OpenAI-Compatible API", lifespan=lifespan)

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    # メッセージを辞書形式に変換
    messages_dict = [m.model_dump() for m in request.messages]
    
    # Orchestrator で推論を実行
    result = await orchestrator.submit_chat_completion(messages_dict, stream=request.stream)
    
    if isinstance(result, dict) and "choices" in result:
        raw_message = result["choices"][0]["message"]
        
        # 🎯 修正の核心: content が空でも reasoning があればそれを採用する
        content = raw_message.get("content", "")
        reasoning = raw_message.get("reasoning", "")
        
        # content が空で reasoning がある場合、それを回答として扱う
        if not content and reasoning:
            content = reasoning
            
        # それでも空の場合のフォールバック
        if not content:
            if "tool_calls" in raw_message:
                content = f"[Tool Call Requested]: {raw_message['tool_calls']}"
            else:
                content = f"[Empty Response] Raw message: {raw_message}"
        
        # クライアントが期待する ChatMessage 形式に整形
        final_message = ChatMessage(role="assistant", content=content)
    else:
        final_message = ChatMessage(role="assistant", content="Error: Invalid response from core.")
    
    return ChatCompletionResponse(
        choices=[
            ChatCompletionResponseChoice(
                index=0,
                message=final_message
            )
        ]
    )

@app.get("/health")
async def health():
    return {"status": "ok", "engine_ready": orchestrator is not None}

if __name__ == "__main__":
    import uvicorn
    # Brownie 既定のポート 8137 で起動
    uvicorn.run(app, host="0.0.0.0", port=8137)