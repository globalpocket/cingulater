import os
import time
import json
from typing import List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator

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
    # --- サーバー起動時 ---
    global orchestrator
    config_path = os.getenv("BROWNIE_CONFIG", "config.yaml")
    logger.info(f"Initializing Brownie Core (Config: {config_path})")
    orchestrator = Orchestrator(config_path)
    
    # MCP ゲートウェイとの接続を開始
    await orchestrator.start()
    logger.info("Brownie Engine is online and connected to MCP Gateway.")
    
    yield  # ここでサーバーがリクエストを処理し続けます
    
    # --- サーバー停止時 ---
    if orchestrator:
        logger.info("Shutting down Brownie Core...")
        await orchestrator.shutdown()

app = FastAPI(title="Brownie OpenAI-Compatible API", lifespan=lifespan)

# StreamingResponseを返す可能性があるため、デコレータの response_model は外します
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    messages_dict = [m.model_dump() for m in request.messages]
    
    # エージェントのワークフローは段階的なストリームが困難なため、内部は強制的に stream=False で一括処理します
    result = await orchestrator.submit_chat_completion(messages_dict, stream=False)
    
    if isinstance(result, dict) and "choices" in result:
        message = result["choices"][0]["message"]
        content = message.get("content")
        
        if not content:
            if "tool_calls" in message:
                content = f"[Tool Call Requested by Model]: {message['tool_calls']}"
            else:
                content = f"[Empty Response] Raw message: {message}"
    else:
        content = "Error: Invalid response from core."
    
    # 1. Continue等のエディタ拡張が要求するストリーミング(SSE)レスポンスのサポート
    if request.stream:
        async def generate():
            chunk_id = f"chatcmpl-{int(time.time())}"
            # 完了したテキストを1つの巨大なチャンクとして送信
            chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": "stop"
                    }
                ]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
            
        return StreamingResponse(generate(), media_type="text/event-stream")

    # 2. 通常のJSONレスポンス
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
    return {"status": "ok", "engine_ready": orchestrator is not None}