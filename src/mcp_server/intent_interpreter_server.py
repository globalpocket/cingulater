import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal

from loguru import logger

from pydantic_ai import Agent
from src.prompts.library import INTENT_DIRECTOR_PROMPT
from src.utils.llm import get_robust_model, wait_for_llm_ready

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# --- 型定義 ---

class IntentDraft(BaseModel):
    """
    ユーザーの意図を整理した下書き (Phase 0)
    """
    status: Literal["approved", "pending"] = Field(
        description="ユーザーの指示が『承認済み・実行可能』か『まだ確認が必要』か"
    )
    intent_summary: str = Field(..., description="ユーザーの要求を1文で要約したもの")
    evaluation_axes: List[str] = Field(
        ..., description="このタスクの成功を判断するための評価軸（3つ程度）"
    )
    required_mcp_servers: List[str] = Field(
        default_factory=list,
        description="このタスクの解決に必要な MCP サーバーのリスト"
    )
    draft_comment: str = Field(
        ...,
        description=(
            "ユーザーに確認を求めるための丁寧な返信メッセージ。"
            "status='approved' の場合は内部的な要約として使用され、ユーザーには投稿されません。"
        ),
    )

# --- サーバー定義 ---

# ロギング設定
logger = setup_logging("intent_interpreter_server")
mcp = create_mcp_server("IntentInterpreter")

@mcp.tool()
@mcp_tool_errorhandler
async def interpret_intent(
    instruction: str, model_name: str, endpoint: str
) -> Dict[str, Any]:
    """
    ユーザーの指示を分析し、実行フェーズに進むべきか確認が必要かを判断します。
    (Pydantic-AI を使用して型安全に解析します)
    """
    logger.info(f"Interpreting intent via Pydantic-AI Agent: {instruction[:100]}...")

    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "pending"}

    # モデルの取得
    model = get_robust_model(model_name, base_url=endpoint)
    
    # Intent Analysis Agent の構築
    agent = Agent(
        model,
        result_type=IntentDraft,
        system_prompt=INTENT_DIRECTOR_PROMPT
    )

    # 実行
    result = await agent.run(instruction)
    draft = result.output

    logger.debug(f"Intent analysis completed: {draft.status}")
    return draft.model_dump()

if __name__ == "__main__":
    mcp.run(transport="stdio")
