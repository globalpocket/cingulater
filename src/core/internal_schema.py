# src/core/schema.py
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class InternalFunctionCall(BaseModel):
    name: str
    arguments: str = ""

class InternalToolCall(BaseModel):
    id: str
    type: str = "function"
    function: InternalFunctionCall

class InternalMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[InternalToolCall]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None

class InternalTool(BaseModel):
    type: str = "function"
    function: Dict[str, Any]

class InternalAgentRequest(BaseModel):
    model: str = "default"
    messages: List[InternalMessage]
    tools: Optional[List[InternalTool]] = None
    stream: bool = False
    max_tokens: int = Field(default=8192)

    model_config = {
        # Orchestratorが利用する可能性のある、OpenAI固有の追加パラメータ（temperatureなど）を保持できるように許容する
        "extra": "allow"
    }