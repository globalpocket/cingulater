import logging
from typing import TypedDict, List, Dict, Any, Optional
from pydantic_ai import Agent
from pydantic import BaseModel, Field
from src.llm.robust_model import get_robust_model, wait_for_llm_ready
from src.utils.config_loader import get_config

logger = logging.getLogger(__name__)

class IntentDraft(BaseModel):
    """
    ユーザーの意図を整理した下書き
    """
    intent_summary: str = Field(description="ユーザーの要求を1文で要約したもの")
    evaluation_axes: List[str] = Field(description="このタスクの成功を判断するための評価軸（3つ程度）")
    required_mcp_servers: List[str] = Field(description="実行に必要と思われる MCP サーバー名")
    draft_comment: str = Field(description="ユーザーに確認を求めるための丁寧な返信メッセージ")

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
    logger.info("--- Intent Alignment Node ---")
    
    # 設定の読み込み
    config = get_config()
    model_name = config['llm']['models']['planner']
    endpoint = config['llm']['planner_endpoint']
    
    # サーバーの準備完了を待機
    await wait_for_llm_ready(endpoint)
    
    model = get_robust_model(
        model_name,
        base_url=endpoint
    )
    
    agent = Agent(
        model,
        output_type=IntentDraft,
        system_prompt=(
            "あなたは Brownie AI の意図調整フェーズ（Phase 0）を担当するエージェントです。\n"
            "ユーザーからの指示を分析し、何を達成すべきか、成功基準は何か、どのツールが必要かを整理してください。\n"
            "出力は必ず IntentDraft 形式（JSON）で行ってください。"
        )
    )
    
    try:
        result = await agent.run(state['instruction'])
        draft: IntentDraft = result.output
        
        # ユーザーへの最終メッセージを構築
        formatted_draft = draft.draft_comment
        
        # もし要約や評価軸が空でなければ、詳細として付加する
        if draft.intent_summary:
            formatted_draft += (
                f"\n\n---\n"
                f"**🎯 整理された目標:** {draft.intent_summary}\n"
                f"**✅ 成功基準:** " + ", ".join(draft.evaluation_axes) + "\n"
                f"**🛠 使用ツール候補:** " + ", ".join(draft.required_mcp_servers)
            )

        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": formatted_draft,
            "evaluation_axes": draft.evaluation_axes,
            "required_mcp_servers": draft.required_mcp_servers,
            "history": [{"node": "intent_alignment", "status": "draft_updated"}]
        }
    except Exception as e:
        logger.warning(f"Failed to generate structured intent draft: {e}")
        # Fallback: 構造化出力に失敗しても、最低限元の指示をベースに進行させる
        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": f"以下の意図で受け承りました: {state['instruction']}\n\n(自動要約に失敗したため、そのままの内容で確認をお願いします。)",
            "evaluation_axes": ["要件適合性", "破壊的変更の有無"],
            "required_mcp_servers": [],
            "history": [{"node": "intent_alignment", "status": "draft_failed_fallback"}]
        }
