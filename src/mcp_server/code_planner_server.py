"""
BROWNIE Code Planner MCP Server
==============================
設計担当（Planner）の役割を MCP プロトコルで公開するサーバー。
指示を受け取り、詳細な実装設計図（Blueprint）を生成する。
"""

import os
from typing import Optional, Union

from loguru import logger

from .base_server import (
    create_mcp_server,
    mcp_tool_errorhandler,
    override_config_from_argv,
    setup_logging,
)

logger = setup_logging(__name__)

# --- サーバーインスタンスの生成 ---
mcp = create_mcp_server("Code Planner")

# --- グローバル設定（起動時に初期化） ---
_config = {
    "model_name": os.environ.get("BROWNIE_PLANNER_MODEL", "gpt-4o"),
    "endpoint": os.environ.get("BROWNIE_PLANNER_ENDPOINT", "http://localhost:8080/v1"),
    "language": os.environ.get("BROWNIE_LANGUAGE", "Japanese")
}

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from src.prompts.library import PLANNER_SYSTEM_PROMPT
from src.utils.llm import get_robust_model, wait_for_llm_ready

# --- 型定義 ---
class Blueprint(BaseModel):
    logic_constraints: list[str]
    prohibited_actions: list[str]
    context_snippets: Optional[list[dict[str, str]]] = None

def _get_agent():
    """Planner Agent インスタンスの生成"""
    model = get_robust_model(_config["model_name"], base_url=_config["endpoint"])
    
    agent = Agent(
        model,
        result_type=Union[Blueprint, str],  # Blueprint または ユーザーへの回答メッセージ
        system_prompt=PLANNER_SYSTEM_PROMPT
    )
    return agent

# ============================================================
# MCP Tool: generate_blueprint
# ============================================================
@mcp.tool()
@mcp_tool_errorhandler
async def generate_blueprint(instruction: str, context: Optional[str] = None) -> str:
    """ユーザーの指示とコンテキストから、詳細な実装設計図（Blueprint）を生成します。

    Args:
        instruction: ユーザーからの修正・開発指示
        context: 関連するコードやリポジトリの状態などの追加文脈（任意）
    """
    logger.info(f"Generating blueprint for instruction: {instruction[:50]}...")
    
    # サーバーの準備完了を待機
    await wait_for_llm_ready(_config["endpoint"])
    
    agent = _get_agent()
    
    prompt = f"Instruction: {instruction}"
    if context:
        prompt += f"\n\nContext:\n{context}"
        
    result = await agent.run(prompt)
    if isinstance(result.data, Blueprint):
        return result.data.model_dump_json(indent=2)
    else:
        return str(result.data)

# ============================================================
# サーバー起動エントリーポイント
# ============================================================
if __name__ == "__main__":
    override_config_from_argv(_config, ["model_name", "endpoint"])
        
    logger.info(
        f"Code Planner Server initialized: model={_config['model_name']}, "
        f"endpoint={_config['endpoint']}"
    )
    mcp.run(transport="stdio")
