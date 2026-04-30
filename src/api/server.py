# src/api/server.py
import os
import time
import json
import uuid
from typing import List, Optional, Union, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator
from core.events import (
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)

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

def _ensure_openai_id(internal_id: str) -> str:
    """クライアントが期待する厳格なOpenAI IDフォーマット (call_ + 24文字) をサーバー層で保証する"""
    if internal_id and internal_id.startswith("call_") and len(internal_id) >= 20:
        return internal_id
    return f"call_{uuid.uuid4().hex[:24]}"

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Brownie Engine is not initialized")

    request_data = request.model_dump(exclude_none=True)
    
    for m in request_data.get("messages", []):
        if isinstance(m.get("content"), list):
            text_parts = []
            for part in m["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            m["content"] = "\n".join(text_parts)
    
    model_name = request.model

    if request.stream:
        async def generate():
            try:
                base_chunk = {
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name
                }
                
                is_first_chunk = True
                
                async for event in orchestrator.process_workflow(request_data):
                    chunk = base_chunk.copy()
                    
                    if isinstance(event, TextDeltaEvent):
                        delta = {"content": event.content}
                        if is_first_chunk:
                            delta["role"] = "assistant"
                            is_first_chunk = False
                        chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
                        yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                    elif isinstance(event, ToolCallStartEvent):
                        client_id = _ensure_openai_id(event.id)
                        delta = {
                            "tool_calls": [{
                                "index": event.index,
                                "id": client_id,
                                "type": "function",
                                "function": {"name": event.tool_name, "arguments": ""}
                            }]
                        }
                        if is_first_chunk:
                            delta["role"] = "assistant"
                            is_first_chunk = False
                        chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
                        yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                    elif isinstance(event, ToolCallDeltaEvent):
                        chunk["choices"] = [{"index": 0, "delta": {
                            "tool_calls": [{
                                "index": event.index,
                                "function": {"arguments": event.arguments}
                            }]
                        }, "finish_reason": None}]
                        yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                    elif isinstance(event, SystemToolCallEvent):
                        # API仕様（OpenAI互換）に合わせて、システム発行イベントをStart(名前)とDelta(引数)の2チャンクに分割し、IDフォーマットを保証する
                        client_id = _ensure_openai_id(event.id)
                        start_delta = {
                            "tool_calls": [{
                                "index": event.index,
                                "id": client_id,
                                "type": "function",
                                "function": {
                                    "name": event.tool_name, 
                                    "arguments": ""
                                }
                            }]
                        }
                        if is_first_chunk:
                            start_delta["role"] = "assistant"
                            is_first_chunk = False
                        
                        start_chunk = base_chunk.copy()
                        start_chunk["choices"] = [{"index": 0, "delta": start_delta, "finish_reason": None}]
                        yield f"data: {json.dumps(start_chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                        delta_chunk = base_chunk.copy()
                        delta_chunk["choices"] = [{"index": 0, "delta": {
                            "tool_calls": [{
                                "index": event.index,
                                "function": {
                                    "arguments": json.dumps(event.arguments, ensure_ascii=False, separators=(',', ':'))
                                }
                            }]
                        }, "finish_reason": None}]
                        yield f"data: {json.dumps(delta_chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                    elif isinstance(event, ErrorEvent):
                        chunk["choices"] = [{"index": 0, "delta": {"content": f"\n\n[Brownie Error: {event.message}]\n\n"}, "finish_reason": "error"}]
                        yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        
                    elif isinstance(event, WorkflowFinishEvent):
                        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": event.finish_reason}]
                        yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"

            except Exception as e:
                logger.error(f"Streaming error: {e}")
                err_chunk = {
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": f"Internal Server Error: {e}"}, "finish_reason": "error"}]
                }
                yield f"data: {json.dumps(err_chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
            finally:
                yield "data: [DONE]\n\n"
                
        return StreamingResponse(generate(), media_type="text/event-stream")

    else:
        full_content = ""
        tool_calls_dict = {}
        finish_reason = "stop"
        has_error = False
        error_message = ""
        
        try:
            async for event in orchestrator.process_workflow(request_data):
                if isinstance(event, TextDeltaEvent):
                    full_content += event.content
                elif isinstance(event, ToolCallStartEvent):
                    tool_calls_dict[event.index] = {
                        "id": _ensure_openai_id(event.id),
                        "type": "function",
                        "function": {"name": event.tool_name, "arguments": ""}
                    }
                elif isinstance(event, ToolCallDeltaEvent):
                    if event.index in tool_calls_dict:
                        tool_calls_dict[event.index]["function"]["arguments"] += event.arguments
                elif isinstance(event, SystemToolCallEvent):
                    tool_calls_dict[event.index] = {
                        "id": _ensure_openai_id(event.id),
                        "type": "function",
                        "function": {"name": event.tool_name, "arguments": json.dumps(event.arguments, ensure_ascii=False, separators=(',', ':'))}
                    }
                elif isinstance(event, WorkflowFinishEvent):
                    finish_reason = event.finish_reason
                elif isinstance(event, ErrorEvent):
                    has_error = True
                    error_message = event.message
                    finish_reason = "error"
        except Exception as e:
            logger.error(f"Error processing workflow: {e}")
            raise HTTPException(status_code=500, detail=str(e))
            
        if has_error:
            full_content += f"\n\nERROR: {error_message}"

        message = {"role": "assistant"}
        if full_content:
            message["content"] = full_content
        else:
            message["content"] = None

        if tool_calls_dict:
            message["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

        return ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(**message),
                    finish_reason=finish_reason
                )
            ]
        )

@app.get("/health")
async def health():
    return {"status": "ok", "engine_ready": orchestrator is not None}