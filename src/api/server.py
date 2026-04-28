import os
import time
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator

app = FastAPI(title="Brownie OpenAI-Compatible API")
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

@app.on_event("startup")
async def startup_event():
    """サーバー起動時に Orchestrator を初期化し、MCP ゲートウェイに接続する"""
    global orchestrator
    config_path = os.getenv("BROWNIE_CONFIG", "config/config.yaml")
    logger.info(f"Initializing Brownie Core (Config: {config_path})")
    orchestrator = Orchestrator(config_path)
    
    # MCP ゲートウェイとの接続を開始
    await orchestrator.start()
    logger.info("Brownie Engine is online and connected to MCP Gateway.")

@app.on_event("shutdown")
async def shutdown_event():
    """サーバー停止時に接続を安全にクリーンアップする"""
    global orchestrator
    if orchestrator:
        logger.info("Shutting down Brownie Core...")
        await orchestrator.shutdown()

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    messages_dict = [m.model_dump() for m in request.messages]
    result = await orchestrator.submit_chat_completion(messages_dict, stream=request.stream)
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8137)