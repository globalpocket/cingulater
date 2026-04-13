import operator
from typing import TypedDict, List, Dict, Any, Optional, Annotated

class TaskState(TypedDict):
    """
    Brownie 5-Phase Architecture 状態定義
    """
    # 基本情報
    task_id: str
    instruction: str
    repo_path: str
    status: str  # Phase 名や 'Completed', 'Failed', 'Waiting' 等
    
    # Phase 0: Intent Alignment
    intent_confirmed: bool
    evaluation_axes: List[str]
    intent_draft: str
    required_mcp_servers: List[str]
    
    # Phase 1: Core Analysis
    dependency_tree: Dict[str, Any]
    analysis_data: Dict[str, Any]
    high_info_gain_questions: List[str]
    
    # Phase 2: Handshake
    target_specialized_agents: List[str]
    agent_specific_schemas: Dict[str, Any] # エージェントごとの Pull スキーマ
    validated_plan: Dict[str, Any]
    
    # Phase 3: Execution
    execution_tasks: List[str] # Huey に投入予定のタスクID
    execution_logs: List[Dict[str, Any]]
    execution_result_summary: str
    
    # Phase 4: Governance & Repair
    repair_needed: bool
    error_context: Optional[str]
    ringi_document: Optional[str] # 稟議書 (Human-in-the-loop 用)
    governance_decision: Optional[str] # 'Approve', 'Reject', 'NeedsRevision'
    
    # Phase 5: 実行・完了情報
    topic_branch: Optional[str]
    has_changes: bool
    test_results: Optional[Dict[str, Any]]
    pr_url: Optional[str]
    
    # 履歴とログ
    reported_nodes: Annotated[List[str], operator.add]
    history: Annotated[List[Dict[str, Any]], operator.add]
    metadata: Dict[str, Any]
