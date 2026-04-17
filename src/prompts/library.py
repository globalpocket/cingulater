"""
BROWNIE プロジェクトのプロンプト・ライブラリ。
Jinja2 テンプレートを廃止し、Python コード内での型安全な管理へ移行しました。
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("brownie.prompts")

PROMPT_DIR = Path(__file__).parent

def load_prompt(filename: str) -> str:
    """Markdown ファイルからシステムプロンプトを読み込む。"""
    path = PROMPT_DIR / filename
    if not path.exists():
        logger.error(f"Prompt file not found: {path}")
        return f"System prompt error: {filename} not found."
    return path.read_text(encoding="utf-8")

# 各担当者用の動的読み込みプロパティ/関数
def get_planner_prompt() -> str:
    return load_prompt("planner.md")

def get_executor_prompt() -> str:
    return load_prompt("executor.md")

def get_intent_analyst_prompt() -> str:
    return load_prompt("intent_analyst.md")

def get_tool_architect_prompt() -> str:
    return load_prompt("tool_architect.md")

def get_intent_director_prompt() -> str:
    return load_prompt("intent_director.md")

# 後方互換性および簡易アクセス用（必要に応じて）
PLANNER_SYSTEM_PROMPT = get_planner_prompt()
EXECUTOR_SYSTEM_PROMPT = get_executor_prompt()
INTENT_ANALYST_PROMPT = get_intent_analyst_prompt()
TOOL_ARCHITECT_PROMPT = get_tool_architect_prompt()
INTENT_DIRECTOR_PROMPT = get_intent_director_prompt()
