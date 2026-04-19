from typing import Any, Dict

from loguru import logger


async def intent_alignment_node(
    state: Dict[str, Any], workflows: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Phase 0: Intent Alignment Node
    ロジックを IntentInterpreterServer (MCP) へ委譲し、Core は状態遷移のみを担当する。
    """
    logger.info("--- Intent Alignment Node (Delegated to MCP) ---")

    if "interpreter" not in workflows:
        logger.error("Interpreter workflow is not available.")
        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": "システムエラー：意図解析ワークフローがロードされていません。",
            "evaluation_axes": [],
            "required_mcp_servers": [],
            "history": [{"node": "intent_alignment", "status": "error"}],
        }

    interpreter_wf = workflows["interpreter"]

    try:
        # YAML ワークフローを実行
        # WorkflowManager は {'results': {'analyze': ...}, ...} 形式の辞書を返す
        wf_result = await interpreter_wf(input_data=state["instruction"])

        # 'analyze' ノードの出力を取得 (Pydantic-AI の result.output)
        draft_data = wf_result.get("results", {}).get("analyze")

        if not draft_data:
            raise ValueError("Interpreter workflow returned no analysis data.")

        # 以降のロジックは従来通り (互換性維持)

        draft_status = draft_data.get("status")
        draft_comment = draft_data.get("draft_comment", "")
        is_approval = (draft_status == "approved") or (not draft_comment.strip())

        # ユーザーへの返信メッセージの構築
        formatted_draft = None
        if not is_approval:
            formatted_draft = draft_data.get("draft_comment")
            summary = draft_data.get("intent_summary")
            if summary:
                axes = ", ".join(draft_data.get("evaluation_axes", []))
                servers = ", ".join(draft_data.get("required_mcp_servers", []))
                formatted_draft += (
                    f"\n\n---\n"
                    f"**🎯 整理された目標:** {summary}\n"
                    f"**✅ 成功基準:** {axes}\n"
                    f"**🛠 使用ツール候補:** {servers}"
                )

        logger.info(
            f"Intent Alignment Result via MCP: status={draft_data.get('status')}"
        )

        return {
            "status": "InQueue" if is_approval else "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": is_approval,
            "intent_draft": formatted_draft,
            "evaluation_axes": draft_data.get("evaluation_axes", []),
            "required_mcp_servers": draft_data.get("required_mcp_servers", []),
            "history": [
                {
                    "node": "intent_alignment",
                    "status": "approved" if is_approval else "pending",
                    "summary": draft_data.get("intent_summary"),
                }
            ],
        }

    except Exception as e:
        logger.error(f"Intent alignment via MCP failed: {e}")
        err_msg = (
            "意図の解析中にエラーが発生しました。\n"
            "恐れ入りますが、再度指示をお願いします。\n"
            f"Detail: {str(e)}"
        )
        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": err_msg,
            "evaluation_axes": [],
            "required_mcp_servers": [],
            "history": [{"node": "intent_alignment", "status": "error"}],
        }
