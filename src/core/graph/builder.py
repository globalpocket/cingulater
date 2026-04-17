from typing import Literal

from langgraph.graph import END, StateGraph

from src.core.graph.nodes.analysis import core_analysis_node
from src.core.graph.nodes.completion import completion_node
from src.core.graph.nodes.execution import execution_delegation_node
from src.core.graph.nodes.governance import governance_node
from src.core.graph.nodes.handshake import dynamic_handshake_node
from src.core.graph.nodes.intent import intent_alignment_node
from src.core.state_manager import TaskState


def create_brownie_graph():
    """
    BROWNIE 5-Phase ワークフローの構築 (Prebuilt 最適化版)
    """
    workflow = StateGraph(TaskState)

    # 1. 意図調整 (Intent Alignment)
    workflow.add_node("intent_alignment", intent_alignment_node)
    # 2. コア解析 (Core Analysis)
    workflow.add_node("core_analysis", core_analysis_node)
    # 3. 動的ハンドシェイク (Dynamic Handshake)
    workflow.add_node("dynamic_handshake", dynamic_handshake_node)
    # 4. 実行委譲 (Execution Delegation)
    workflow.add_node("execution_delegation", execution_delegation_node)
    # 5. ガバナンス/検証 (Governance)
    workflow.add_node("governance", governance_node)
    # 6. 完了処理 (Completion)
    workflow.add_node("completion", completion_node)

    # --- グラフ配線 ---
    workflow.set_entry_point("intent_alignment")

    # Phase 0 -> Phase 1 分岐
    workflow.add_conditional_edges(
        "intent_alignment",
        lambda state: "core_analysis" if state.get("intent_confirmed") else END,
        {"core_analysis": "core_analysis", END: END}
    )

    # Phase 1 -> Phase 2 分岐 (外部再開待機時は END)
    workflow.add_conditional_edges(
        "core_analysis",
        lambda state: "dynamic_handshake" if state.get("status") == "Phase1_Completed" else END,
        {"dynamic_handshake": "dynamic_handshake", END: END}
    )

    # Phase 2 -> Phase 3
    workflow.add_edge("dynamic_handshake", "execution_delegation")

    # Phase 3 -> Phase 4 分岐 (外部再開待機時は END)
    workflow.add_conditional_edges(
        "execution_delegation",
        lambda state: "governance" if state.get("status") in ["Execution_Completed", "Execution_Failed"] else END,
        {"governance": "governance", END: END}
    )

    # Phase 4 -> ループ or 完了
    def route_governance(state: TaskState) -> Literal["completion", "intent_alignment", "governance"]:
        decision = state.get("governance_decision")
        status = state.get("status")

        if decision == "Approve":
            return "completion"
        if decision == "Reject":
            return "intent_alignment"
        
        # 修復中または再検証が必要な場合は自己ループ
        if status in ["Waiting_Repair", "Repair_Completed"]:
            return "governance"
            
        return "governance"

    workflow.add_conditional_edges(
        "governance",
        route_governance,
        {
            "completion": "completion",
            "intent_alignment": "intent_alignment",
            "governance": "governance"
        }
    )

    # 完了 -> 終了
    workflow.add_edge("completion", END)

    return workflow


def compile_workflow(checkpointer=None):
    """
    ワークフローのコンパイル。
    ガバナンスフェーズ（人間または再検証の介入）の直前で割り込む設定。
    """
    builder = create_brownie_graph()
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["governance"]
    )
