from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Literal

class IntentDraft(BaseModel):
    """
    ユーザーの意図を整理した下書き (Phase 0)
    """
    status: Literal["approved", "pending"] = Field(
        description="ユーザーの指示が『承認済み・実行可能』か『まだ確認が必要』か"
    )
    intent_summary: str = Field(..., description="ユーザーの要求を1文で要約したもの")
    evaluation_axes: List[str] = Field(
        ..., description="このタスクの成功を判断するための評価軸（3つ程度）"
    )
    required_mcp_servers: List[str] = Field(
        default_factory=list,
        description="このタスクの解決に必要な MCP サーバーのリスト"
    )
    draft_comment: str = Field(
        ..., description="ユーザーに確認を求めるための丁寧な返信メッセージ。status='approved' の場合は内部的な要約として使用され、ユーザーには投稿されません。"
    )

class AnalysisProposal(BaseModel):
    """
    分析計画 (Phase 1)
    """
    dependency_critical_nodes: List[str] = Field(..., description="解析すべき重要コンポーネント")
    questions_to_user: List[str] = Field(..., description="不確実性を排除するための質問リスト")

class RingiDocument(BaseModel):
    """
    稟議書 (Phase 4)
    """
    summary: str = Field(..., description="発生した事象の概要")
    impact_analysis: str = Field(..., description="影響範囲の分析")
    proposed_fix: str = Field(..., description="具体的な修正案")
    risk_assessment: str = Field(..., description="リスク評価")

# --- Blueprint 定義 (設計思想: 決定論的な JSON 連携) ---

class BlueprintFile(BaseModel):
    path: str = Field(..., description="修正または作成対象のファイルパス")
    purpose: str = Field(..., description="そのファイルに対する変更の目的")

class Blueprint(BaseModel):
    """
    Planner から Executor へ渡される厳格な設計図。
    Vector通信（文脈の垂れ流し）を廃止し、この構造体のみで指示を完結させます。
    """
    target_files: List[BlueprintFile] = Field(..., description="操作対象ファイルの一覧")
    logic_constraints: List[str] = Field(..., description="実装すべきロジックの制約条件")
    prohibited_actions: List[str] = Field(..., description="禁止事項・変更不可な箇所")
    context_snippets: Optional[List[Dict[str, str]]] = Field(None, description="参考にするコード片 (file, snippet)")
