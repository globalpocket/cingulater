import logging
import sys
from typing import Any, Dict, List, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent

# ロギング設定
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("intent_interpreter_server")

# FastMCP サーバーの初期化
mcp = FastMCP("IntentInterpreter")

# --- データモデルの定義 ---

class IntentDraft(BaseModel):
    """
    ユーザーの意図を整理した下書き
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

# --- ツール定義 ---

@mcp.tool()
async def interpret_intent(
    instruction: str, model_name: str, endpoint: str
) -> Dict[str, Any]:
    """
    ユーザーの指示を分析し、実行フェーズに進むべきか確認が必要かを判断します。
    """
    logger.info(f"Interpreting intent for instruction: {instruction[:50]}...")

    # PydanticAI Agent の初期化
    # 外部からモデル名とエンドポイントを受け取ることで柔軟性を確保
    from src.core.agent import get_robust_model, wait_for_llm_ready
    
    await wait_for_llm_ready(endpoint)
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
            "   - 明らかに矛盾した指示があり、実行すると危険が伴う場合。\n"
            "   - `status` を 'pending' にし、`draft_comment` に丁寧な確認メッセージを"
            "記述してください。"
        ),
    )

    try:
        result = await agent.run(instruction)
        # 辞書形式で返すことで MCP クライアント側で扱いやすくする
        return result.data.model_dump()
    except Exception as e:
        logger.error(f"Intent interpretation failed: {e}")
        return {
            "status": "pending",
            "intent_summary": "Error during analysis",
            "evaluation_axes": [],
            "required_mcp_servers": [],
            "draft_comment": f"解析中にエラーが発生しました: {str(e)}"
        }

if __name__ == "__main__":
    mcp.run(transport="stdio")
