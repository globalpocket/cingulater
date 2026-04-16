import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal

from loguru import logger

from src.core.workflow_manager import WorkflowLoader
from src.utils.llm import wait_for_llm_ready

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

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
logger = setup_logging("intent_interpreter_server")
mcp = create_mcp_server("IntentInterpreter")

# 動的ワークフローローダーの初期化
# ルート直下の workflows ディレクトリをスキャン
project_root = Path(os.getcwd())
loader = WorkflowLoader(project_root)
# 初期化時にワークフローをロードしておく
workflow_registry = loader.load_all()

@mcp.tool()
@mcp_tool_errorhandler
async def reload_workflows() -> str:
    """登録されている動的ワークフローを再読み込みし、最新の状態に更新します。"""
    global workflow_registry
    workflow_registry = loader.reload()
    logger.info(f"Workflows reloaded. Current count: {len(workflow_registry)}")
    return f"Workflows reloaded. {len(workflow_registry)} workflows found."

@mcp.tool()
@mcp_tool_errorhandler
async def interpret_intent(
    instruction: str, model_name: str, endpoint: str
) -> Dict[str, Any]:
    """
    ユーザーの指示を分析し、実行フェーズに進むべきか確認が必要かを判断します。
    (動的ワークフロー 'intent_alignment' を使用して実行します)
    """
    logger.info(f"Interpreting intent via dynamic workflow: {instruction[:100]}...")

    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "pending"}

    # ワークフローの取得
    workflow_tool = workflow_registry.get("intent_alignment")
    if not workflow_tool:
        logger.error("Workflow 'intent_alignment' not found in registry.")
        return {
            "status": "pending",
            "intent_summary": "Workflow error",
            "draft_comment": "エラー：意図解析ワークフローが見つかりませんでした。構成を確認してください。",
            "evaluation_axes": [],
            "required_mcp_servers": []
        }

    # ワークフローの実行
    # DynamicWorkflowState が返される
    state_result = await workflow_tool(
        input_data=instruction,
        model_name=model_name,
        endpoint=endpoint
    )
    
    # 最終ノード 'node3_draft_reply' の出力を取得
    results = state_result.get("results", {})
    final_output = results.get("node3_draft_reply", "")
    
    if not final_output:
        raise ValueError("Final node output is empty.")

    # JSON を抽出 (AI が Markdown ブロックを作ってしまう可能性を考慮)
    clean_json = str(final_output).strip()
    if "```json" in clean_json:
        clean_json = clean_json.split("```json")[-1].split("```")[0].strip()
    elif "```" in clean_json:
        clean_json = clean_json.split("```")[-1].split("```")[0].strip()

    logger.debug(f"Raw workflow output: {final_output}")
    data = json.loads(clean_json)
    
    # Pydantic モデルでバリデーション
    draft = IntentDraft.model_validate(data)
    return draft.model_dump()

if __name__ == "__main__":
    mcp.run(transport="stdio")
