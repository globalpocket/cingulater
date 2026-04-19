import os
from typing import Any, Dict

from loguru import logger

from src.core.agent import GitHubClientWrapper
from src.core.config import get_settings
from src.core.state_manager import TaskState
from src.core.workers.tasks import repair_task


async def governance_node(
    state: TaskState, workflows: Dict[str, Any], mcp_manager: Any
) -> Dict[str, Any]:
    """
    Phase 4: Governance & Fail-Safe
    報告書（稟議書）の生成を GovernanceServer (MCP) へ委譲し、
    Core は GitHub への投稿とステート管理のみを行う。
    """
    logger.info(f"--- Phase 4: Governance & Ringi ({state['task_id']}) ---")

    if "governance" not in workflows:
        logger.error("Governance workflow is not available.")
        return {
            "status": "Failed",
            "history": [{"node": "governance", "status": "error"}],
        }

    governance_wf = workflows["governance"]

    # 実行失敗かつ修復がまだの場合
    if state.get("status") == "Execution_Failed" and not state.get("ringi_document"):
        logger.info(f"Execution failed. Repairing {state['task_id']}...")
        repo_name = state["task_id"].split("#")[0]
        issue_number = int(state["task_id"].split("#")[1])
        err_ctx = state.get("error_context", "Unknown error")
        repair_task(state["task_id"], repo_name, issue_number, err_ctx)
        return {
            "status": "Waiting_Repair",
            "history": [{"node": "governance", "status": "repair_enqueued"}],
        }

    # すでに Ringi を投稿済みかチェック (二重投稿防止)
    current_status = state.get("status")
    if not state.get("governance_decision") and current_status != "WaitingForApproval":
        # 1. 稟議書の作成 (Phase 9: ワークフローへの委譲)
        try:
            test_out = (
                state.get("test_results", {}).get("stdout")
                if state.get("test_results")
                else "No test results."
            )

            # ワークフローを実行し、稟議書と判断結果を生成
            wf_result = await governance_wf(
                input_data={
                    "task_id": state["task_id"],
                    "status": state.get("status", "Unknown"),
                    "has_changes": state.get("has_changes", False),
                    "test_results": test_out,
                }
            )
            results = wf_result.get("results", {})
            ringi_content = results.get("ringi_document", "Review required.")
            risk_level = results.get("risk_level", "UNKNOWN")

            # 2. GitHub に投稿
            gh_token = os.getenv("GITHUB_TOKEN", "")
            gh = GitHubClientWrapper(gh_token, mcp_manager)
            repo_name = state["task_id"].split("#")[0]
            issue_number = int(state["task_id"].split("#")[1])

            await gh.post_comment(
                repo_name,
                issue_number,
                f"### [Risk: {risk_level}]\n{ringi_content}\n{get_settings().footer}",
            )

            return {
                "status": "WaitingForApproval",
                "ringi_document": ringi_content,
                "metadata": {"risk_level": risk_level, "governance_result": results},
                "history": [{"node": "governance", "status": "ringi_posted"}],
            }
        except Exception as e:
            logger.error(f"Governance workflow failed: {e}")
            return {
                "status": "WaitingForApproval",
                "history": [{"node": "governance", "status": "failed"}],
            }

    # 承認済みかどうかをチェック
    if state.get("governance_decision") == "Approve":
        return {
            "status": "Approved",
            "history": [{"node": "governance", "status": "approved"}],
        }

    return {
        "status": "WaitingForApproval",
        "history": [{"node": "governance", "status": "waiting_decision"}],
    }
