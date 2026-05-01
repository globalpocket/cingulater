# src/core/llm_client.py
import json
from typing import Optional, List, AsyncGenerator, Protocol
from pydantic import BaseModel
import httpx
from loguru import logger

class ToolCallChunk(BaseModel):
    """ツール呼び出しの差分（デルタ）を表現するクラス"""
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[str] = None

class StandardLLMChunk(BaseModel):
    """LLMからストリーミングされる標準化されたチャンクデータ"""
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCallChunk]] = None
    finish_reason: Optional[str] = None

class LLMClientProtocol(Protocol):
    async def stream_chat(
        self, 
        endpoint: str, 
        payload: dict,
        timeout: int
    ) -> AsyncGenerator[StandardLLMChunk, None]:
        """
        指定されたエンドポイントにペイロードを送信し、
        StandardLLMChunk を非同期ジェネレータとして段階的に返す。
        """
        ...

class OpenAILLMClient(LLMClientProtocol):
    """OpenAI API互換のレスポンスを解釈し、標準化チャンクに変換するクライアント"""
    
    async def stream_chat(
        self, 
        endpoint: str, 
        payload: dict,
        timeout: int
    ) -> AsyncGenerator[StandardLLMChunk, None]:
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{endpoint}/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise Exception(f"LLM Error {resp.status_code}: {error_text.decode('utf-8', errors='ignore')}")
                
                content_type = resp.headers.get("content-type", "")
                
                # 1. Non-streaming (Fallback) の場合
                if "application/json" in content_type:
                    body = await resp.aread()
                    try:
                        full_json = json.loads(body)
                        for choice in full_json.get("choices", []):
                            message = choice.get("message", {})
                            chunk = StandardLLMChunk()
                            
                            if "content" in message and message["content"]:
                                chunk.content = message["content"]
                            
                            if "tool_calls" in message:
                                chunk.tool_calls = []
                                for idx, tc in enumerate(message["tool_calls"]):
                                    fn_name = tc.get("function", {}).get("name")
                                    args_str = tc.get("function", {}).get("arguments", "{}")
                                    tc_id = tc.get("id", f"call_{idx}")
                                    chunk.tool_calls.append(ToolCallChunk(
                                        index=idx,
                                        id=tc_id,
                                        name=fn_name,
                                        arguments=args_str
                                    ))
                                    
                            chunk.finish_reason = choice.get("finish_reason", "stop")
                            yield chunk
                            
                    except Exception as e:
                        raise Exception(f"Failed to parse fallback JSON: {e}")
                
                # 2. Streaming (SSE) の場合
                else:
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                            
                        if line.startswith("data: "):
                            try:
                                chunk_data = json.loads(line[6:])
                                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                                chunk = StandardLLMChunk()
                                
                                if "content" in delta and isinstance(delta["content"], str) and delta["content"]:
                                    chunk.content = delta["content"]
                                
                                if "tool_calls" in delta:
                                    chunk.tool_calls = []
                                    for tc in delta["tool_calls"]:
                                        idx = tc.get("index", 0)
                                        fn_name = tc.get("function", {}).get("name")
                                        fn_args = tc.get("function", {}).get("arguments")
                                        tc_id = tc.get("id")
                                        
                                        chunk.tool_calls.append(ToolCallChunk(
                                            index=idx,
                                            id=tc_id,
                                            name=fn_name,
                                            arguments=fn_args
                                        ))
                                        
                                chunk_fr = chunk_data.get("choices", [{}])[0].get("finish_reason")
                                if chunk_fr:
                                    chunk.finish_reason = chunk_fr
                                    
                                yield chunk
                            except json.JSONDecodeError:
                                pass