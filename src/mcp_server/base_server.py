"""
BROWNIE MCP Server Base Infrastructure
======================================
全MCPサーバーで共通して利用する基底ロジック。
"""

import functools
import sys
from typing import Any, Callable, Dict, List, TypeVar

from fastmcp import FastMCP
from loguru import logger

T = TypeVar("T")


def mcp_tool_errorhandler(func: Callable[..., Any]) -> Callable[..., Any]:
    """MCPツールの標準例外ハンドラデコレータ。
    エラーをロギングし、エラーメッセージを文字列として返します。
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Error executing tool '{func.__name__}': {str(e)}")
            return f"Error: {str(e)}"

    return wrapper


def create_mcp_server(name: str) -> FastMCP:
    """FastMCP インスタンスを生成します。"""
    return FastMCP(name)


def setup_logging(name: str):
    """ロギングの標準設定を行います (後方互換性のために loguru.logger を返します)。"""
    return logger.bind(name=name)


def override_config_from_argv(config: Dict[str, Any], keys: List[str]):
    """コマンドライン引数から設定項目を順番に上書きします。"""
    for i, key in enumerate(keys):
        if len(sys.argv) > i + 1:
            config[key] = sys.argv[i + 1]
