from typing import Any, Dict

from src.core.state_manager import TaskState

logger = logging.getLogger(__name__)


async def dynamic_handshake_node(
    state: TaskState, workflows: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Phase 2: Handshake (動的プランニング)
    """
    print(f"--- Phase 2: Dynamic Handshake ({state['task_id']}) ---")

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
        needs_info = results.get("needs_info", False)

        # GitHub に投稿 (エンジン側の共通機能を利用)
        import os

        from src.core.agent import GitHubClientWrapper

        gh = GitHubClientWrapper(os.getenv("GITHUB_TOKEN", ""))
        repo_name = state["task_id"].split("#")[0]
        issue_number = int(state["task_id"].split("#")[1])

        from src.core.config import get_settings

        await gh.post_comment(
            repo_name, issue_number, f"{greeting}\n{get_settings().footer}"
        )

        return {
            "status": "Phase2_HandshakeDone",
            "metadata": {"handshake_result": results},
            "history": [{"node": "dynamic_handshake", "status": "completed"}],
        }
    except Exception as e:
        print(f"Handshake failed: {e}")
        return {
            "status": "Phase2_HandshakeDone",
            "history": [{"node": "dynamic_handshake", "status": "failed_with_error"}],
        }
