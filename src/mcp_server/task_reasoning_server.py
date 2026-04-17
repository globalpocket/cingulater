import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.core.workflow_manager import WorkflowLoader
from src.utils.config_loader import get_config
from src.utils.llm import get_robust_model, wait_for_llm_ready

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

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

mcp = create_mcp_server("TaskReasoning")

# --- 思考プロセス管理用のプロキシ ---
class SequentialThinkingProxy:
    def __init__(self):
        self.client: Optional[Client] = None

    async def _get_client(self) -> Client:
        if self.client:
            return self.client
        
        logger.info("Initializing official Sequential Thinking MCP sub-server...")
        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-sequential-thinking"]
        )
        self.client = Client(transport)
        await self.client.initialize()
        return self.client

thinking_proxy = SequentialThinkingProxy()

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
    公式の Sequential Thinking ツールを使用して論理的な思考ステップを踏みます。
    """
    logger.info(f"Starting reasoning loop for {task_id} using {model_name}")
    
    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "failed"}

    # モデルの初期化
    _model = get_robust_model(model_name, base_url=endpoint)
    
    # 公式 Sequential Thinking ツールの取得
    thinking_client = await thinking_proxy._get_client()
    # 思考ツールの名称変更を避けるため、そのまま注入
    # 実際には Pydantic AI のツールとしてラップする（簡略化のため一旦スタブ的に扱う）
    
from pydantic_ai import Agent
from src.prompts.library import PLANNER_SYSTEM_PROMPT
from src.utils.llm import get_robust_model, wait_for_llm_ready

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

# --- 型定義 ---

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

mcp = create_mcp_server("TaskReasoning")

# --- 思考プロセス管理用のプロキシ ---
class SequentialThinkingProxy:
    def __init__(self):
        self.client: Optional[Client] = None

    async def _get_client(self) -> Client:
        if self.client:
            return self.client
        
        logger.info("Initializing official Sequential Thinking MCP sub-server...")
        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-sequential-thinking"]
        )
        self.client = Client(transport)
        await self.client.initialize()
        return self.client

thinking_proxy = SequentialThinkingProxy()

# ロギング設定
logger = setup_logging("task_reasoning_server")

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
    タスクを解決するための自律的な推論ループ（Planner）を実行し、設計図（Blueprint）を生成します。
    (Pydantic-AI を使用して型安全に推論します)
    """
    logger.info(f"Starting reasoning loop for {task_id} using {model_name}")
    
    # LLM の準備を待機
    ready = await wait_for_llm_ready(endpoint)
    if not ready:
        return {"error": "LLM server not ready", "status": "failed"}

    # モデルの初期化
    _model = get_robust_model(model_name, base_url=endpoint)
    
    # 公式 Sequential Thinking ツールの取得 (将来的な Pydantic AI ツール統合の準備)
    _thinking_client = await thinking_proxy._get_client()

    # Pydantic AI Agent (Planner) の構築
    agent = Agent(
        _model,
        result_type=Blueprint,
        system_prompt=PLANNER_SYSTEM_PROMPT
    )

    # 実行 (現在は同期的な Blueprint 生成をデモ)
    result = await agent.run(instruction)
    blueprint = result.output
    
    # ダミーのファイルリスト生成 (実際の推論結果に基づいて生成される想定)
    blueprint_files = [BlueprintFile(path="Implementation", purpose="Apply planned changes")]
    
    return {
        "status": "finished",
        "task_id": task_id,
        "blueprint": blueprint.model_dump(),
        "files": [f.model_dump() for f in blueprint_files],
        "summary": "Reasoning loop completed with structured Blueprint output."
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
