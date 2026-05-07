# src/api/server.py
import os
import time
import json
import uuid
import asyncio
from typing import List, Optional, Union, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from core.orchestrator import Orchestrator
from core.schema import InternalAgentRequest, InternalMessage, InternalToolCall, InternalFunctionCall, InternalTool
from core.events import (
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)

orchestrator: Optional[Orchestrator] = None
chat_lock: Optional[asyncio.Lock] = None
KEEP_ALIVE_INTERVAL = 15.0  # 追加: Keep-Aliveの送信間隔

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
    model: str = "cingulater-v2"
    messages: List[ChatMessage]
    tools: Optional[List[Dict[str, Any]]] = None
    stream: bool = False
    max_tokens: Optional[int] = None

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
    model: str = "cingulater-v2"
    choices: List[ChatCompletionResponseChoice]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, chat_lock
    # イベントループ内で初期化するためにlifespanでLockを生成する
    chat_lock = asyncio.Lock()
    
    config_path = os.getenv("CINGULATER_CONFIG", "config.yaml")
    logger.info(f"Initializing Cingulater Core (Config: {config_path})")
    orchestrator = Orchestrator(config_path)
    
    await orchestrator.start()
    logger.info("Cingulater Engine is online and connected to MCP Gateway.")
    
    yield
    
    if orchestrator:
        logger.info("Shutting down Cingulater Core...")
        await orchestrator.shutdown()

app = FastAPI(title="Cingulater OpenAI-Compatible API", lifespan=lifespan)

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

@asynccontextmanager
async def _optional_task_lock():
    """設定に応じてタスクを直列化する条件付きロック"""
    if orchestrator and getattr(orchestrator.settings.agent, "single_task_mode", False) and chat_lock:
        logger.debug("[Concurrency] single_task_mode is ON. Acquiring lock...")
        async with chat_lock:
            yield
    else:
        yield

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Cingulater Engine is not initialized")

    internal_messages = []
    for m in request.messages:
        content = m.content
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            content = "\n".join(text_parts)
        
        tool_calls = None
        if m.tool_calls:
            tool_calls = [
                InternalToolCall(
                    id=tc.id,
                    type=tc.type,
                    function=InternalFunctionCall(name=tc.function.name, arguments=tc.function.arguments)
                ) for tc in m.tool_calls
            ]

        internal_messages.append(InternalMessage(
            role=m.role,
            content=content,
            tool_calls=tool_calls,
            name=m.name,
            tool_call_id=m.tool_call_id
        ))

    internal_tools = None
    if request.tools:
        internal_tools = [InternalTool(**t) for t in request.tools]

    internal_req = InternalAgentRequest(
        model=request.model,
        messages=internal_messages,
        tools=internal_tools,
        stream=request.stream,
        max_tokens=request.max_tokens if request.max_tokens is not None else 8192,
        **(request.model_extra or {})
    )
    
    model_name = request.model

    if request.stream:
        async def generate():
            # stream全体のライフサイクルをロックで保護する
            async with _optional_task_lock():
                pending_task = None
                try:
                    base_chunk = {
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name
                    }
                    
                    is_first_chunk = True
                    
                    workflow_iterator = aiter(orchestrator.process_workflow(internal_req))
                    
                    async def _get_next(it):
                        return await anext(it)

                    while True:
                        if pending_task is None:
                            # タスク化してキャンセルを防ぐ
                            pending_task = asyncio.create_task(_get_next(workflow_iterator))
                        
                        # タスクが完了するか、タイムアウトになるまで待つ
                        done, pending = await asyncio.wait([pending_task], timeout=KEEP_ALIVE_INTERVAL)
                        
                        if pending_task in done:
                            try:
                                event = pending_task.result()
                                pending_task = None  # 次のイベント取得に向けてリセット
                            except StopAsyncIteration:
                                break
                            except Exception as e:
                                raise e
                        else:
                            # タイムアウトした場合は Keep-Alive を送って次のループへ
                            yield ": keep-alive\n\n"
                            continue
                        
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
                            delta = {"content": f"\n\n[Cingulater Error: {event.message}]\n\n"}
                            if is_first_chunk:
                                delta["role"] = "assistant"
                                is_first_chunk = False
                            chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": "error"}]
                            yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                            
                        elif isinstance(event, WorkflowFinishEvent):
                            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": event.finish_reason}]
                            yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"

                except Exception as e:
                    logger.error(f"Streaming error: {e}")
                    delta = {"content": f"Internal Server Error: {e}"}
                    if is_first_chunk:
                        delta["role"] = "assistant"
                        is_first_chunk = False
                        
                    err_chunk = {
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": "error"}]
                    }
                    yield f"data: {json.dumps(err_chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
                finally:
                    if pending_task and not pending_task.done():
                        pending_task.cancel()
                    yield "data: [DONE]\n\n"
                
        return StreamingResponse(generate(), media_type="text/event-stream")

    else:
        # 非ストリーム処理時もロックで保護する
        async with _optional_task_lock():
            full_content = ""
            tool_calls_dict = {}
            finish_reason = "stop"
            has_error = False
            error_message = ""
            
            try:
                async for event in orchestrator.process_workflow(internal_req):
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