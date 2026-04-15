"""
BROWNIE Code Planner MCP Server
==============================
設計担当（Planner）の役割を MCP プロトコルで公開するサーバー。
指示を受け取り、詳細な実装設計図（Blueprint）を生成する。
"""

import os
import sys
import logging
import asyncio
from typing import Optional, Union, List, Dict, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from src.core.types import Blueprint, BlueprintFile
from src.llm.robust_model import get_robust_model, wait_for_llm_ready

logger = logging.getLogger(__name__)

# --- サーバーインスタンスの生成 ---
mcp = FastMCP("Code Planner")

# --- グローバル設定（起動時に初期化） ---
_config = {
    "model_name": os.environ.get("BROWNIE_PLANNER_MODEL", "gpt-4o"),
    "endpoint": os.environ.get("BROWNIE_PLANNER_ENDPOINT", "http://localhost:8080/v1"),
    "language": os.environ.get("BROWNIE_LANGUAGE", "Japanese")
}

def _get_agent():
    """Planner Agent インスタンスの生成"""
    model = get_robust_model(_config["model_name"], base_url=_config["endpoint"])
    
    agent = Agent(
        model,
        result_type=Union[Blueprint, str],  # Blueprint または ユーザーへの回答メッセージ
        system_prompt=(
            "あなたは高度なソフトウェアアーキテクト（Planner）です。\n"
            "ユーザーの指示を分析し、具体的かつ厳密な実装設計図（Blueprint）を作成することが任務です。\n\n"
            "### 設計の指針 ###\n"
            "1. **決定論的設計**: 曖昧さを排し、Executor が迷わず実装できる詳細度で記述してください。\n"
            "2. **最小変更原則**: 必要最小限のファイル変更で目的を達成してください。\n"
            "3. **制約の明示**: 実装において守るべきロジックの制約や禁止事項を必ず含めてください。\n\n"
            f"報告や思考は原則として {_config['language']} で行ってください。"
        )
    )
    return agent

# ============================================================
# MCP Tool: generate_blueprint
# ============================================================
@mcp.tool()
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
        
    try:
        result = await agent.run(prompt)
        if isinstance(result.data, Blueprint):
            return result.data.model_dump_json(indent=2)
        else:
            return str(result.data)
    except Exception as e:
        logger.error(f"Failed to generate blueprint: {e}")
        return f"Error: {str(e)}"

# ============================================================
# サーバー起動エントリーポイント
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    
    # 引数による設定のオーバーライド（オプション）
    if len(sys.argv) > 1:
        _config["model_name"] = sys.argv[1]
    if len(sys.argv) > 2:
        _config["endpoint"] = sys.argv[2]
        
    logger.info(f"Code Planner Server initialized: model={_config['model_name']}, endpoint={_config['endpoint']}")
    mcp.run(transport="stdio")
