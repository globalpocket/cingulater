from typing import Any, Dict

from loguru import logger

from src.core.state_manager import TaskState


async def dynamic_handshake_node(
    state: TaskState, workflows: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Phase 2: Handshake (動的プランニング)
    """
    logger.info(f"--- Phase 2: Dynamic Handshake ({state['task_id']}) ---")

    if "handshake" not in workflows:
        logger.error("Handshake workflow is not available.")
        return {
            "status": "Failed",
            "history": [{"node": "dynamic_handshake", "status": "error"}],
        }

    handshake_wf = workflows["handshake"]

    try:
        # Pydantic-AI ワークフローを実行し、ユーザーへの挨拶/ヒアリング内容を生成
        # 入力として instruction を渡す
        wf_result = await handshake_wf(input_data=state["instruction"])
        results = wf_result.get("results", {})
        greeting = results.get("greeting", "Hello! I'm starting the task.")

        # GitHub に投稿 (一元化された GitHubClient を利用)
        from src.core.base import get_global_orchestrator

        gorch = get_global_orchestrator()
        if not gorch:
            logger.error("Global orchestrator not found in handshake node.")
            return {
                "status": "Failed",
                "history": [{"node": "dynamic_handshake", "status": "error"}],
            }

        repo_name = state["task_id"].split("#")[0]
        issue_number = int(state["task_id"].split("#")[1])

        await gorch.gh_client.post_comment(
            repo_name, issue_number, f"{greeting}\n{gorch.settings.footer}"
        )

        return {
            "status": "Phase2_HandshakeDone",
            "metadata": {"handshake_result": results},
            "history": [{"node": "dynamic_handshake", "status": "completed"}],
        }
    except Exception as e:
        logger.error(f"Handshake failed: {e}")
        return {
            "status": "Phase2_HandshakeDone",
            "history": [{"node": "dynamic_handshake", "status": "failed_with_error"}],
        }
