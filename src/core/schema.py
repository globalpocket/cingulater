# src/core/schema.py
from typing import List, Optional, Dict, Any, Union
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
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
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
    
    # クライアント指定の推論パラメータを透過的に受け取るための定義
    system_message: Optional[str] = None
    user_message: Optional[str] = None
    developer_message: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    response_format: Optional[Dict[str, Any]] = None
    reasoning_effort: Optional[str] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    parallel_tool: Optional[bool] = None

    model_config = {
        "extra": "allow"
    }