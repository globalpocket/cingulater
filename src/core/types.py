from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskState(BaseModel):
    """
    推論グラフ (State Graph) の共有ステータス。
    Core に残すべき唯一の動的な型定義。
    """

    task_id: str
    instruction: str = ""
    repo_path: Optional[str] = None
    status: str = "InQueue"
    history: List[Dict[str, Any]] = Field(default_factory=list)

    # Phase 0/1: Intent & Analysis
    intent_confirmed: bool = False
    intent_summary: Optional[str] = None
    intent_draft: Optional[str] = None
    evaluation_axes: List[str] = Field(default_factory=list)
    required_mcp_servers: List[str] = Field(default_factory=list)

    analysis_proposal: Optional[Dict[str, Any]] = None
    validated_plan: Optional[str] = None

    # Platform Generic metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Governance & Results
    governance_decision: Optional[str] = None

    has_changes: bool = False
    topic_branch: Optional[str] = None
    test_results: Dict[str, Any] = Field(default_factory=dict)

    error_context: Optional[str] = None
