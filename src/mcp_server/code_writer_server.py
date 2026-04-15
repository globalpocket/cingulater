"""
BROWNIE Code Writer MCP Server
==============================
実装担当（Executor）の役割を MCP プロトコルで公開するサーバー。
設計図（Blueprint）を受け取り、具体的な実装コードを生成する。
"""

import os
import sys
import logging
import asyncio
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from pydantic_ai import Agent

from src.core.types import Blueprint
from src.llm.robust_model import get_robust_model, wait_for_llm_ready

logger = logging.getLogger(__name__)

# --- サーバーインスタンスの生成 ---
mcp = FastMCP("Code Writer")

# --- グローバル設定（起動時に初期化） ---
_config = {
    "model_name": os.environ.get("BROWNIE_EXECUTOR_MODEL", "gpt-4o"),
    "endpoint": os.environ.get("BROWNIE_EXECUTOR_ENDPOINT", "http://localhost:8081/v1"),
    "language": os.environ.get("BROWNIE_LANGUAGE", "Japanese")
}

def _get_agent():
    """Executor Agent インスタンスの生成"""
    model = get_robust_model(_config["model_name"], base_url=_config["endpoint"])
    
    agent = Agent(
        model,
        system_prompt=(
            "あなたは高度なソフトウェアエンジニア（Executor）です。\n"
            "Planner から渡される「Strict Blueprint（厳格な設計図）」は絶対のルールです。\n"
            "設計図に記載されていない独自の解釈、機能追加、リファクタリングは厳禁です。\n"
            "回答は実装コード案のみとし、ツール呼び出しは一切行わず、純粋な Markdown で返してください。\n\n"
            f"報告や解説が必要な場合は、原則として {_config['language']} で記述してください。"
        )
    )
    return agent

# ============================================================
# MCP Tool: generate_code
# ============================================================
@mcp.tool()
async def generate_code(blueprint_json: str) -> str:
    """設計図（Blueprint）を解析し、具体的な実装コードを生成します。

    Args:
        blueprint_json: Planner から生成された設計図の JSON 文字列
    """
    logger.info("Generating code from blueprint...")
    
    # サーバーの準備完了を待機
    await wait_for_llm_ready(_config["endpoint"])
    
    try:
        # JSON 文字列から Blueprint オブジェクトを復元（検証のため）
        blueprint = Blueprint.model_validate_json(blueprint_json)
        prompt = f"### STRICT BLUEPRINT ###\n{blueprint.model_dump_json(indent=2)}"
        
        agent = _get_agent()
        result = await agent.run(prompt)
        return str(result.data)
        
    except Exception as e:
        logger.error(f"Failed to generate code: {e}")
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
        
    logger.info(f"Code Writer Server initialized: model={_config['model_name']}, endpoint={_config['endpoint']}")
    mcp.run(transport="stdio")
