from langgraph.graph import StateGraph, END
from src.core.graph.state import TaskState
from src.core.graph.nodes.intent import intent_alignment_node
from src.core.graph.nodes.analysis import core_analysis_node
from src.core.graph.nodes.handshake import dynamic_handshake_node
from src.core.graph.nodes.execution import execution_delegation_node
from src.core.graph.nodes.governance import governance_node
from src.core.graph.nodes.completion import completion_node # 追加

def create_brownie_graph():
    """
    Brownie 5-Phase ワークフローの構築
    """
    builder = StateGraph(TaskState)

    # ノードの追加
    builder.add_node("intent_alignment", intent_alignment_node)
    builder.add_node("core_analysis", core_analysis_node)
    builder.add_node("dynamic_handshake", dynamic_handshake_node)
    builder.add_node("execution_delegation", execution_delegation_node)
    builder.add_node("governance", governance_node)
    builder.add_node("completion", completion_node) # 追加

    # エッジと遷移ロジック
    builder.set_entry_point("intent_alignment")
    
    # Phase 0 -> Phase 1
    builder.add_edge("intent_alignment", "core_analysis")
    
    # Phase 1: Analysis Waiting Loop
    def route_after_analysis(state: TaskState) -> str:
        if state.get("status") == "Phase1_Completed":
            return "dynamic_handshake"
        # 修正: ループして待機するのではなく、一旦ワークフローを終了して外部からの再開を待つ
        return END 
    
    builder.add_conditional_edges("core_analysis", route_after_analysis, {
        "dynamic_handshake": "dynamic_handshake",
        END: END
    })
    
    # Phase 2 -> Phase 3
    builder.add_edge("dynamic_handshake", "execution_delegation")
    
    # Phase 3: Execution Waiting Loop
    def route_after_execution(state: TaskState) -> str:
        status = state.get("status")
        if status in ["Execution_Completed", "Execution_Failed"]:
            return "governance"
        # 修正: 待機時はグラフを抜ける
        return END
        
    builder.add_conditional_edges("execution_delegation", route_after_execution, {
        "governance": "governance",
        END: END
    })
    
    # Phase 4 からの条件分岐
    def route_after_governance(state: TaskState) -> str:
        status = state.get("status")
        decision = state.get("governance_decision")
        
        if decision == "Approve":
            return "completion" # 承認時は完了ノード（PR作成）へ
        elif decision == "Reject":
            return "intent_alignment" # 却下時は最初に戻る
        elif status == "Waiting_Repair" or status == "Repair_Completed":
            return "governance"
        return "governance"

    builder.add_conditional_edges(
        "governance",
        route_after_governance,
        {
            "completion": "completion",
            "intent_alignment": "intent_alignment",
            "governance": "governance"
        }
    )
    
    # Completion -> END
    builder.add_edge("completion", END)

    return builder

def compile_workflow(checkpointer=None):
    """
    ワークフローのコンパイル。
    Phase 4 (Governance) の直前で割り込む。
    """
    builder = create_brownie_graph()
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["core_analysis", "governance"]
    )
