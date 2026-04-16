from loguru import logger
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

import os
from pathlib import Path
from pydantic_ai import Agent
from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
from src.utils.llm import get_robust_model, wait_for_llm_ready
from src.core.workflow_manager import WorkflowLoader
from src.utils.config_loader import get_config

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

logger = setup_logging("task_reasoning_server")
mcp = create_mcp_server("TaskReasoning")

# --- WorkflowManager の初期化とツール登録 ---
project_root = os.getenv("BROWNIE_PROJECT_ROOT", ".")
workspace_root = os.getenv("BROWNIE_WORKSPACE_ROOT")
config_path = os.getenv("BROWNIE_CONFIG_PATH")

loader = WorkflowLoader(
    Path(project_root), 
    Path(workspace_root) if workspace_root else None
)

# Config のロード
config = None
if config_path:
    config = get_config(config_path)

# 動的ツールのロード
dynamic_tools = loader.load_all(config=config)

# サーバーのツールとして登録
for name, func in dynamic_tools.items():
    mcp.add_tool(func)
    logger.info(f"Dynamically registered tool: {name}")

# 共有エージェント（プランナー）
# 実際の実装では、ここで他の MCP サーバーへのクライアントを構築し、
# ツールをラップして提供する。
# ここでは一旦、Core の CoderAgent 相当の構造を模倣する。

@mcp.tool()
@mcp_tool_errorhandler
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
    
    # ノード実行用の Pydantic AI Agent (Planner)
    # 動的ツールを注入
    agent = Agent(
        _model,
        tools=list(dynamic_tools.values()),
        system_prompt=f"You are a BROWNIE Task Planner. Instruction: {instruction}"
    )
    
    # 実際の実装ではここでエージェントを実行する (現在はスタブの Blueprint を返す)
    # res = await agent.run(instruction)
    
    # ダミーの Blueprint 生成 (実装が進むにつれ本物の Agent 実行に置き換え)
    blueprint = Blueprint(
        logic_constraints=["Use professional tone"],
        prohibited_actions=["Do not delete existing sections"]
    )
    # BlueprintFile の修正（以前の表示ミスを修正）
    blueprint_files = [BlueprintFile(path="README.md", purpose="Update documentation")]
    
    return {
        "status": "finished",
        "task_id": task_id,
        "blueprint": blueprint.model_dump(),
        "files": [f.model_dump() for f in blueprint_files],
        "summary": "Reasoning loop completed with dynamic tools available."
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
