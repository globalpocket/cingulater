from typing import Any, Dict

from src.core.state_manager import TaskState


async def dynamic_handshake_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 2: Dynamic Discovery & Handshake
    実行エージェントのスキーマを取得し、実行計画とのマッピングを行う。
    """
    print(f"--- Phase 2: Dynamic Handshake ({state['task_id']}) ---")

    # グローバルオーケストレーターからワークフローを取得 (Phase 9: 深層ドメイン抽出)
    from src.core.orchestrator import global_orchestrator

    if (
        not global_orchestrator
        or "handshake" not in global_orchestrator.dynamic_workflows
    ):
        print("Error: Handshake workflow is missing.")
        return {
            "status": "Phase2_HandshakeDone",
            "history": [
                {"node": "dynamic_handshake", "status": "no_workflow_fallback"}
            ],
        }

    handshake_wf = global_orchestrator.dynamic_workflows["handshake"]

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

        from src.utils.config_loader import get_footer

        await gh.post_comment(repo_name, issue_number, f"{greeting}\n{get_footer()}")

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
