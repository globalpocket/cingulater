from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskState(BaseModel):
    """
    推論グラフ (State Graph) の共有ステータス。
    Core に残すべき唯一の動的な型定義。
    """

    task_id: str
    status: str = "InQueue"
    history: List[Dict[str, Any]] = Field(default_factory=list)

    # 以下は各 MCP サーバーから返される JSON データを保持する
    intent_confirmed: bool = False
    intent_summary: Optional[str] = None
    intent_draft: Optional[str] = None

    analysis_proposal: Optional[Dict[str, Any]] = None
    validated_plan: Optional[str] = None

    ringi_document: Optional[str] = None
    governance_decision: Optional[str] = None

    has_changes: bool = False
    topic_branch: Optional[str] = None
    test_results: Dict[str, Any] = Field(default_factory=dict)

    error_context: Optional[str] = None
