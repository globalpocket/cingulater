from functools import partial
from typing import Any, Dict, Literal

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from src.core.graph.nodes.analysis import core_analysis_node
from src.core.graph.nodes.completion import completion_node
from src.core.graph.nodes.execution import execution_delegation_node
from src.core.graph.nodes.governance import governance_node
from src.core.graph.nodes.handshake import dynamic_handshake_node
from src.core.graph.nodes.intent import intent_alignment_node
from src.core.state_manager import TaskState


def create_brownie_graph(workflows: Dict, mcp_manager: Any):
    """
    BROWNIE 5-Phase ワークフローの構築 (LangGraph Prebuilt 最適化版)
    """
    workflow = StateGraph(TaskState)

    # 1. 基本ノードの登録
    workflow.add_node(
        "intent_alignment", 
        partial(intent_alignment_node, workflows=workflows)
    )
    workflow.add_node(
        "core_analysis", 
        partial(core_analysis_node, workflows=workflows)
    )
    workflow.add_node(
        "dynamic_handshake", 
        partial(dynamic_handshake_node, workflows=workflows)
    )
    workflow.add_node(
        "execution_delegation", 
        partial(execution_delegation_node, mcp_manager=mcp_manager)
    )
    workflow.add_node(
        "governance", 
        partial(governance_node, workflows=workflows, mcp_manager=mcp_manager)
    )
    workflow.add_node(
        "completion", 
        partial(completion_node, workflows=workflows)
    )

    # 2. ツール実行ノード (Prebuilt)
    if mcp_manager and hasattr(mcp_manager, "tools"):
        tools = mcp_manager.tools
        workflow.add_node("tools", ToolNode(tools))

    # --- グラフ配線 (宣言的ルーティング) ---
    workflow.set_entry_point("intent_alignment")

    # Phase 0: Intent (承認されたら Analysis へ、そうでなければ終了)
    workflow.add_conditional_edges(
        "intent_alignment",
        lambda state: "core_analysis" if state.get("intent_confirmed") else END,
        {"core_analysis": "core_analysis", END: END},
    )

    # Phase 1: Core Analysis -> Handshake
    workflow.add_conditional_edges(
        "core_analysis",
        lambda state: (
            "dynamic_handshake" if state.get("status") == "Phase1_Completed" else END
        ),
        {"dynamic_handshake": "dynamic_handshake", END: END},
    )

    # Phase 2: Handshake -> Execution
    workflow.add_edge("dynamic_handshake", "execution_delegation")

    # Phase 3: Execution -> Governance (ツールの結果を待つ場合はツールノードへ)
    path_map = {"governance": "governance", END: END}
    if "tools" in workflow.nodes:
        path_map["tools"] = "tools"

    workflow.add_conditional_edges(
        "execution_delegation",
        tools_condition,  # Prebuilt による自動ツールルーティング
        path_map,
    )

    # ツール実行後は Execution ノードに戻って結果を処理する
    if "tools" in workflow.nodes:
        workflow.add_edge("tools", "execution_delegation")

    # Phase 4: Governance -> Completion / Loop / Repair
    def route_governance(
        state: TaskState,
    ) -> Literal["completion", "intent_alignment", "governance", END]:
        decision = state.get("governance_decision")
        status = state.get("status")

        if decision == "Approve":
            return "completion"
        if decision == "Reject":
            return "intent_alignment"

        # 外部の介入や待機が必要な場合はグラフを抜ける
        if status in ["Waiting_Human_Feedback", "Waiting_Repair"]:
            return END

        return "governance"  # 自己修復ループ

    workflow.add_conditional_edges("governance", route_governance)

    # Completion -> END
    workflow.add_edge("completion", END)

    return workflow


def compile_workflow(workflows: Dict, mcp_manager: Any, checkpointer=None):
    """
    ワークフローのコンパイル。
    ガバナンス（承認）プロセスでの割り込み設定を維持。
    """
    builder = create_brownie_graph(workflows=workflows, mcp_manager=mcp_manager)
    return builder.compile(checkpointer=checkpointer, interrupt_before=["governance"])
