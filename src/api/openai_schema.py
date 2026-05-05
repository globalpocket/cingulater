# src/api/schema.py
import time
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field

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
    model: str = "cingulater-v1"
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
    model: str = "cingulater-v1"
    choices: List[ChatCompletionResponseChoice]