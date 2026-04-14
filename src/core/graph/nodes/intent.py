import logging
from typing import TypedDict, List, Dict, Any, Optional, Literal
from pydantic_ai import Agent
from pydantic import BaseModel, Field
from src.llm.robust_model import get_robust_model, wait_for_llm_ready
from src.utils.config_loader import get_config

logger = logging.getLogger(__name__)


class IntentDraft(BaseModel):
    """
    ユーザーの意図を整理した下書き
    """

    status: Literal["approved", "pending"] = Field(
        description="ユーザーの指示が『承認済み・実行可能』か『まだ確認が必要』か"
    )
    intent_summary: str = Field(description="ユーザーの要求を1文で要約したもの")
    evaluation_axes: List[str] = Field(
        description="このタスクの成功を判断するための評価軸（3つ程度）"
    )
    required_mcp_servers: List[str] = Field(
        description="実行に必要と思われる MCP サーバー名"
    )
    draft_comment: str = Field(
        description="ユーザーに確認を求めるための丁寧な返信メッセージ。status='approved' の場合は内部的な要約として使用され、ユーザーには投稿されません。"
    )


class IntentState(TypedDict):
    instruction: str
    status: str
    intent_confirmed: bool
    intent_draft: Optional[str]
    evaluation_axes: List[str]
    required_mcp_servers: List[str]
    history: List[Dict[str, Any]]


async def intent_alignment_node(state: IntentState) -> Dict[str, Any]:
    """
    Phase 0: ユーザーの指示から意図を抽出し、合意を形成するフェーズ
    """
    logger.info("--- Intent Alignment Node (Autonomous Mode Enabled) ---")

    # 設定の読み込み
    config = get_config()
    model_name = config["llm"]["models"]["planner"]
    endpoint = config["llm"]["planner_endpoint"]

    # サーバーの準備完了を待機
    await wait_for_llm_ready(endpoint)

    model = get_robust_model(model_name, base_url=endpoint)

    agent = Agent(
        model,
        output_type=IntentDraft,
        system_prompt=(
            "あなたは Brownie AI の意図調整フェーズ（Phase 0）を担当するエージェントです。\n"
            "ユーザーからの指示を分析し、自律的に『実行（Phase 1）』へ移るべきかどうかを判定してください。\n\n"
            "### 判定基準 ###\n"
            "1. **【承認・お任せ (approved)】**: \n"
            "   - ユーザーが『進めて』『OK』『承認』『好きにして』『お任せ』、\n"
            "     あるいは『直ちに開始せよ』『確認不要』等の意図を示した場合。\n"
            "   - **重要**: この場合、不足している技術的な詳細はあなたが解析フェーズで自ら決定します。質問してはいけません。\n"
            "   - `status` を 'approved' にし、`draft_comment` には内部的な実行理由を書いてください（ユーザーには表示されません）。\n\n"
            "2. **【確認が必要 (pending)】**: \n"
            "   - 全く新しい大きなタスクで、まだ一度も方針を合意していない場合。\n"
            "   - 明らかに矛盾した指示があり、実行すると危険が伴う場合。\n"
            "   - `status` を 'pending' にし、`draft_comment` に丁寧な確認メッセージを記述してください。\n\n"
            "### 特記事項 ###\n"
            "- リポジトリが空で『何か作って』と言われたら、'approved' として一般的なモダンな構成（HTML/CSS等）での構築を開始してください。\n"
            "- ユーザーは同じ質問を繰り返されることを極端に嫌います。一度合意した、または『任せる』と言われた事項を二度と聞き返さないでください。"
        ),
    )

    try:
        # LLMによる意図解析
        result = await agent.run(state["instruction"])
        draft: IntentDraft = result.output

        # 承認検知: LLMが 'approved' と判定したか。
        # 万が一 LLM が status を間違えても、draft_comment が空なら承認とみなす
        is_approval = (draft.status == "approved") or (not draft.draft_comment.strip())

        # ユーザーへの返信メッセージの構築
        # 承認済み（approved）の場合は、GitHub へのコメント投稿を回避するために None を返す
        formatted_draft = None
        if not is_approval:
            formatted_draft = draft.draft_comment
            if draft.intent_summary:
                formatted_draft += (
                    f"\n\n---\n"
                    f"**🎯 整理された目標:** {draft.intent_summary}\n"
                    f"**✅ 成功基準:** " + ", ".join(draft.evaluation_axes) + "\n"
                    "**🛠 使用ツール候補:** " + ", ".join(draft.required_mcp_servers)
                )

        logger.info(
            f"Intent Alignment Result: status={draft.status}, confirmed={is_approval}"
        )

        return {
            "status": "InQueue" if is_approval else "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": is_approval,
            "intent_draft": formatted_draft,
            "evaluation_axes": draft.evaluation_axes,
            "required_mcp_servers": draft.required_mcp_servers,
            "history": [
                {
                    "node": "intent_alignment",
                    "status": "approved" if is_approval else "pending",
                    "summary": draft.intent_summary,
                }
            ],
        }
    except Exception as e:
        logger.error(f"Intent alignment failed: {e}")
        # 構造化出力に失敗した場合は、安全のため確認待ちへ
        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": f"意図の解析中にエラーが発生しました: {state['instruction']}\n恐れ入りますが、再度指示をお願いします。",
            "evaluation_axes": [],
            "required_mcp_servers": [],
            "history": [{"node": "intent_alignment", "status": "error"}],
        }
