from typing import Any, Dict

from src.core.state_manager import TaskState


async def core_analysis_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 1: Core Analysis (全方位分析)
    """
    # グローバルオーケストレーターからワークフローを取得 (Phase 8: 純粋エンジン化)
    from src.core.orchestrator import global_orchestrator
    if not global_orchestrator or "planner" not in global_orchestrator.dynamic_workflows:
        logger.error("Planner workflow is not available.")
        return {
            "status": "Failed",
            "history": [{"node": "core_analysis", "status": "error"}]
        }

    planner_wf = global_orchestrator.dynamic_workflows["planner"]

    try:
        # YAML ワークフローを実行し設計図 (Blueprint) を生成
        wf_result = await planner_wf(input_data=state["instruction"])
        blueprint = wf_result.get("results", {}).get("plan")

        if not blueprint:
            raise ValueError("Planner workflow returned no blueprint.")

        return {
            "status": "Phase1_Completed",
            "analysis_data": blueprint,
            "validated_plan": blueprint, # Phase 2 への受け渡し
            "history": [{"node": "core_analysis", "status": "completed"}]
        }
    except Exception as e:
        logger.error(f"Core analysis failed: {e}")
        return {
            "status": "Failed",
            "metadata": {"error": str(e)},
            "history": [{"node": "core_analysis", "status": "failed"}]
        }
