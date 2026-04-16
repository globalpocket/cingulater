from loguru import logger
import os
from typing import Any, Dict

from src.core.agent import GitHubClientWrapper
from src.core.state_manager import TaskState
from src.core.workers.tasks import repair_task
from src.utils.config_loader import get_footer


async def governance_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 4: Governance & Fail-Safe
    報告書（稟議書）の生成を GovernanceServer (MCP) へ委譲し、
    Core は GitHub への投稿とステート管理のみを行う。
    """
    logger.info(f"--- Phase 4: Governance & Ringi ({state['task_id']}) ---")
    
    # 実行失敗かつ修復がまだの場合
    if state.get("status") == "Execution_Failed" and not state.get("ringi_document"):
        logger.info(f"Execution failed. Repairing {state['task_id']}...")
        repo_name = state['task_id'].split("#")[0]
        issue_number = int(state['task_id'].split("#")[1])
        err_ctx = state.get("error_context", "Unknown error")
        repair_task(state['task_id'], repo_name, issue_number, err_ctx)
        return {
            "status": "Waiting_Repair",
            "history": [{"node": "governance", "status": "repair_enqueued"}]
        }

    # すでに Ringi を投稿済みかチェック (二重投稿防止)
    current_status = state.get("status")
    if not state.get("governance_decision") and current_status != "WaitingForApproval":
        # 1. 稟議書の作成 (GovernanceServer MCP への委譲)
        from src.core.orchestrator import global_orchestrator
        mgr = global_orchestrator.mcp_manager if global_orchestrator else None
        if not mgr or not mgr.governance_client:
            logger.error("Governance MCP Client is not available.")
            return {
                "status": "WaitingForApproval",
                "history": [{"node": "governance", "status": "error_mcp_missing"}]
            }

        client = global_orchestrator.mcp_manager.governance_client
        
            test_out = (
                state.get("test_results", {}).get("stdout")
                if state.get("test_results")
                else None
            )
            # MCP ツールの呼び出し
            ringi_content = await client.call_tool(
                "generate_ringi_sho",
                task_id=state['task_id'],
                status=state.get("status", "Unknown"),
                has_changes=state.get("has_changes", False),
                topic_branch=state.get("topic_branch", "None"),
                test_results_stdout=test_out
            )

            # 2. GitHub に投稿
            gh_token = os.getenv("GITHUB_TOKEN", "")
            gh = GitHubClientWrapper(gh_token, mgr)
            repo_name = state['task_id'].split("#")[0]
            issue_number = int(state['task_id'].split("#")[1])
            
            await gh.post_comment(repo_name, issue_number, ringi_content + get_footer())
            
            return {
                "status": "WaitingForApproval",
                "ringi_document": ringi_content,
                "history": [{"node": "governance", "status": "ringi_posted"}]
            }
        except Exception as e:
            logger.error(f"Governance report generation failed via MCP: {e}")
            return {
                "status": "WaitingForApproval",
                "history": [{"node": "governance", "status": "error"}]
            }

    # 承認済みかどうかをチェック
    if state.get("governance_decision") == "Approve":
        return {
            "status": "Approved",
            "history": [{"node": "governance", "status": "approved"}]
        }
    
    return {
        "status": "WaitingForApproval",
        "history": [{"node": "governance", "status": "waiting_decision"}]
    }
