# src/core/events.py
from pydantic import BaseModel
from typing import Optional, Dict, Any

class AgentEvent(BaseModel):
    """すべての内部イベントの基底クラス"""
    pass

class TextDeltaEvent(AgentEvent):
    """テキストチャンクが生成された際のイベント"""
    content: str

class ToolCallStartEvent(AgentEvent):
    """ツール呼び出しが開始された際のイベント"""
    index: int
    id: str
    tool_name: str

class ToolCallDeltaEvent(AgentEvent):
    """ツール呼び出しの引数（チャンク）が生成された際のイベント"""
    index: int
    arguments: str

class SystemToolCallEvent(AgentEvent):
    """システム(反芻など)が決定した一括のツール呼び出しイベント"""
    index: int
    id: str
    tool_name: str
    arguments: Dict[str, Any]

class WorkflowFinishEvent(AgentEvent):
    """ワークフローが完了した際のイベント"""
    finish_reason: str = "stop"

class ErrorEvent(AgentEvent):
    """エラーが発生した際のイベント"""
    message: str