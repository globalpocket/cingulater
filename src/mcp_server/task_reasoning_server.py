import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.utils.llm import get_robust_model, wait_for_llm_ready

# --- 型定義 (Decentralized from types.py) ---

class BlueprintFile(BaseModel):
    path: str = Field(..., description="修正または作成対象のファイルパス")
    purpose: str = Field(..., description="そのファイルに対する変更の目的")

class Blueprint(BaseModel):
    """
    Planner から Executor へ渡される厳格な設計図。
    """
    logic_constraints: List[str] = Field(
        ..., description="実装すべきロジックの制約条件"
    )
    prohibited_actions: List[str] = Field(
        ..., description="禁止事項・変更不可な箇所"
    )
    context_snippets: Optional[List[Dict[str, str]]] = Field(
        None, description="参考にするコード片"
    )

# --- サーバー定義 ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("task_reasoning_server")

mcp = FastMCP("TaskReasoning")

# 共有エージェント（プランナー）
# 実際の実装では、ここで他の MCP サーバーへのクライアントを構築し、
# ツールをラップして提供する。
# ここでは一旦、Core の CoderAgent 相当の構造を模倣する。

@mcp.tool()
async def execute_reasoning_loop(
    instruction: str,
    task_id: str,
    repo_name: str,
    issue_number: int,
    model_name: str,
    endpoint: str,
    context: Optional[str] = None
) -> Dict[str, Any]:
    """
    タスクを解決するための自律的な推論ループ（Planner/Executor）を実行します。
    """
    logger.info(f"Starting reasoning loop for {task_id} using {model_name}")
    
    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "failed"}

    # 実際はここで github-mcp-server や repo-provision-server の
    # ツールをラップしてエージェントに渡す。
    _model = get_robust_model(model_name, base_url=endpoint)
    logger.info(f"Model {_model.model_name} initialized for reasoning.")
    
    # 現在の Core の実装を完全に移行するには、これらへのクライアント接続が必要。
    # ここでは Blueprint を生成して一旦成功を返すスタブ状態から、
    # 徐々に実ロジックへ移行する。
    
    # ダミーの Blueprint 生成 (実装が進むにつれ本物の Agent 実行に置き換え)
    blueprint = Blueprint(
        target_files=[BlueprintFile(path="README.md", purpose="Update documentation")],
        logic_constraints=["Use professional tone"],
        prohibited_actions=["Do not delete existing sections"]
    )
    
    return {
        "status": "finished",
        "task_id": task_id,
        "blueprint": blueprint.model_dump(),
        "summary": "Reasoning loop completed (Stub implementation)"
    }

if __name__ == "__main__":
    mcp.run()
