import logging
import os
import sys
from typing import Dict, Any, List, Literal
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from src.utils.llm import get_robust_model, wait_for_llm_ready

# --- 型定義 (Core から分散) ---

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
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("intent_interpreter_server")

mcp = FastMCP("IntentInterpreter")

@mcp.tool()
async def interpret_intent(
    instruction: str, model_name: str, endpoint: str
) -> Dict[str, Any]:
    """
    ユーザーの指示を分析し、実行フェーズに進むべきか確認が必要かを判断します。
    """
    logger.info(f"Interpreting intent: {instruction[:100]}...")

    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "pending"}

    model = get_robust_model(model_name, base_url=endpoint)

    agent = Agent(
        model,
        result_type=IntentDraft,  # Note: pydantic-ai 最新版では result_type
        system_prompt=(
            "あなたは Brownie AI の意図調整フェーズを担当するエージェントです。\n"
            "ユーザーからの指示を分析し、自律的に『実行（Phase 1）』へ移るべきかどうかを"
            "判定してください。\n\n"
            "### 判定基準 ###\n"
            "1. **【承認・お任せ (approved)】**: \n"
            "   - ユーザーが『進めて』『OK』『承認』『お任せ』、あるいは"
            "『直ちに開始せよ』等の意図を示した場合。\n"
            "   - **重要**: 不足している技術的な詳細はあなたが自ら決定します。"
            "質問してはいけません。\n"
            "   - `status` を 'approved' に設定してください。\n\n"
            "2. **【確認が必要 (pending)】**: \n"
            "   - 全く新しい大きなタスクで、まだ方針を合意していない場合。\n"
            "   - 指示が極めて曖昧で、何をしていいか分からない場合。\n"
            "   - `status` を 'pending' にし、`draft_comment` に丁寧な確認メッセージを"
            "記述してください。"
        ),
    )

    try:
        result = await agent.run(instruction)
        return result.data.model_dump()
    except Exception as e:
        logger.error(f"Intent interpretation failed: {e}")
        return {
            "status": "pending",
            "intent_summary": "Error during analysis",
            "draft_comment": f"申し訳ありません、意図の解析中にエラーが発生しました: {str(e)}",
            "evaluation_axes": [],
            "required_mcp_servers": []
        }

if __name__ == "__main__":
    mcp.run()
