import os
import time
import json
from typing import List, Optional, Union, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator

orchestrator: Optional[Orchestrator] = None

# --- OpenAI Specifications ---
class FunctionCall(BaseModel):
    name: str
    arguments: str

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall

class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    tool_calls: Optional[List[ToolCall]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None

    model_config = {
        "extra": "allow"
    }

class ChatCompletionRequest(BaseModel):
    model: str = "brownie-v2"
    messages: List[ChatMessage]
    stream: bool = False

    model_config = {
        "extra": "allow"
    }

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = "stop"

class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{int(time.time())}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "brownie-v2"
    choices: List[ChatCompletionResponseChoice]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    config_path = os.getenv("BROWNIE_CONFIG", "config.yaml")
    logger.info(f"Initializing Brownie Core (Config: {config_path})")
    orchestrator = Orchestrator(config_path)
    
    await orchestrator.start()
    logger.info("Brownie Engine is online and connected to MCP Gateway.")
    
    yield
    
    if orchestrator:
        logger.info("Shutting down Brownie Core...")
        await orchestrator.shutdown()

app = FastAPI(title="Brownie OpenAI-Compatible API", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
    except Exception:
        body_str = "Could not decode body"
        
    logger.error(f"422 Validation Error: {exc.errors()}")
    logger.error(f"Request Body: {body_str}")
    
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_str},
    )

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    # リクエスト全体を辞書化（tools, temperature, tool_choice等すべて含む）
    request_data = request.model_dump(exclude_none=True)
    
    # 内部エンジン用に content を文字列に正規化
    for m in request_data.get("messages", []):
        if isinstance(m.get("content"), list):
            text_parts = []
            for part in m["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            m["content"] = "\n".join(text_parts)
    
    if request.stream:
        async def generate():
            try:
                stream_gen = await orchestrator.submit_chat_completion(request_data)
                async for chunk in stream_gen:
                    yield f"data: {json.dumps(chunk)}\n\n"
            except Exception as e:
                logger.error(f"Streaming error: {e}")
            finally:
                yield "data: [DONE]\n\n"
                
        return StreamingResponse(generate(), media_type="text/event-stream")

    else:
        result = await orchestrator.submit_chat_completion(request_data)
        
        if not isinstance(result, dict) or "choices" not in result:
            logger.error(f"Invalid orchestrator response: {result}")
            raise HTTPException(status_code=500, detail="Invalid response from core engine.")
        
        message_data = result["choices"][0].get("message", {})
        
        chat_message = ChatMessage(
            role=message_data.get("role", "assistant"),
            content=message_data.get("content"),
            tool_calls=message_data.get("tool_calls")
        )
        
        return ChatCompletionResponse(
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=chat_message,
                    finish_reason=result["choices"][0].get("finish_reason", "stop")
                )
            ]
        )

@app.get("/health")
async def health():
    return {"status": "ok", "engine_ready": orchestrator is not None}