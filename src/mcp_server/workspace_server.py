"""
BROWNIE Workspace MCP Server
==============================
「手足（ファイル操作・コマンド実行）」を MCP プロトコルで公開するサーバー。
stdio トランスポートで Orchestrator のサブプロセスとして動作する。

セキュリティ:
  本サーバーは既存の SandboxManager を import して利用する。
  4層防御（Docker隔離、非Root実行、YAMLサニタイズ、Path Traversal防御）を
  一切再実装せず、完全に継承する。

公開 Tool:
  - set_workspace_root(path): ワークスペースのルートディレクトリを動的に変更
  - list_files(path, max_depth): ファイル一覧取得
  - read_file(path): ファイル内容読み取り
  - write_file(path, content): ファイル書き込み（workspace内のみ）
  - run_command(command): Docker隔離コマンド実行
  - run_semgrep(): Semgrep静的解析
  - lint_code(path): コード品質診断
  - format_code(path): コードフォーマット
  - scan_security(path): セキュリティ診断
"""

import os
import sys

import logging

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# --- サーバーインスタンスの生成 ---
mcp = FastMCP("BrownieWorkspace")

# --- グローバル状態（起動時に初期化） ---
_sandbox = None


def _get_sandbox():
    """SandboxManager のインスタンスを取得（初期化済みであること前提）"""
    if _sandbox is None:
        raise RuntimeError("SandboxManager が初期化されていません。サーバー起動引数を確認してください。")
    return _sandbox


# ============================================================
# MCP Tool: set_workspace_root
# ============================================================
@mcp.tool()
async def set_workspace_root(path: str) -> str:
    """ワークスペースのルートディレクトリを動的に変更します。

    Args:
        path: 新しいワークスペースのルートパス
    """
    sandbox = _get_sandbox()
    sandbox.set_workspace_root(path)
    return f"Workspace root updated to: {path}"


# ============================================================
# MCP Tool: list_files
# ============================================================
@mcp.tool()
async def list_files(path: str = ".", max_depth: int = 1) -> str:
    """指定パスのファイル一覧を表示します。
    大規模リポジトリでは max_depth=1 で階層的探索 (Discovery) を行います。

    Args:
        path: 対象ディレクトリのパス（デフォルト: カレント）
        max_depth: 探索の最大深度（デフォルト: 1）
    """
    sandbox = _get_sandbox()
    return await sandbox.list_files(path, max_depth=int(max_depth))


# ============================================================
# MCP Tool: read_file
# ============================================================
@mcp.tool()
async def read_file(path: str) -> str:
    """指定したファイルの内容を読み取ります。

    Args:
        path: 読み取るファイルのパス
    """
    sandbox = _get_sandbox()
    return await sandbox.read_file(path)


# ============================================================
# MCP Tool: write_file
# ============================================================
@mcp.tool()
async def write_file(path: str, content: str) -> str:
    """ファイルを新規作成または上書きします。
    セキュリティ: workspace ディレクトリ内への書き込みのみ許可されます。

    Args:
        path: 書き込み先ファイルのパス
        content: ファイルの内容
    """
    sandbox = _get_sandbox()
    return await sandbox.write_file(path, content)


# ============================================================
# MCP Tool: run_command
# ============================================================
@mcp.tool()
async def run_command(command: str) -> str:
    """Docker コンテナ内でシェルコマンドを実行します。
    セキュリティ: 非Rootユーザーで実行され、ワークスペースのみマウントされます。

    Args:
        command: 実行するシェルコマンド
    """
    sandbox = _get_sandbox()
    res = await sandbox.run_command(command)
    return f"ExitStatus: {res['exit_code']}\nLogs: {res['logs']}"


# ============================================================
# MCP Tool: run_semgrep
# ============================================================
@mcp.tool()
async def run_semgrep() -> str:
    """Semgrep による静的解析を実行します。
    Docker コンテナ内で実行され、結果を JSON 形式で返します。
    """
    sandbox = _get_sandbox()
    res = await sandbox.run_semgrep("mcp_task")
    return f"Semgrep Analysis Result:\nStatus: {res['status']}\nLogs: {res['logs']}"


# ============================================================
# MCP Tool: lint_code
# ============================================================
@mcp.tool()
async def lint_code(path: str = ".") -> str:
    """Semgrep やリンターを使用してコード品質を診断します。

    Args:
        path: 診断対象のパス（デフォルト: カレント）
    """
    sandbox = _get_sandbox()
    return await sandbox.lint_code(path)


# ============================================================
# MCP Tool: format_code
# ============================================================
@mcp.tool()
async def format_code(path: str = ".") -> str:
    """Black や Prettier 等でコードをフォーマットします。

    Args:
        path: フォーマット対象のパス（デフォルト: カレント）
    """
    sandbox = _get_sandbox()
    return await sandbox.format_code(path)


# ============================================================
# MCP Tool: scan_security
# ============================================================
@mcp.tool()
async def scan_security(path: str = ".") -> str:
    """Bandit 等によるセキュリティ脆弱性をスキャンします。

    Args:
        path: スキャン対象のパス（デフォルト: カレント）
    """
    sandbox = _get_sandbox()
    return await sandbox.scan_security(path)


# ============================================================
# サーバー起動エントリーポイント
# ============================================================
def _init_from_args():
    """コマンドライン引数から SandboxManager を初期化"""
    global _sandbox

    if len(sys.argv) < 5:
        print(
            "Usage: python -m src.mcp.workspace_server <repo_path> <reference_path> <user_id> <group_id>",
            file=sys.stderr
        )
        sys.exit(1)

    repo_path = sys.argv[1]
    reference_path = sys.argv[2]
    user_id = int(sys.argv[3])
    group_id = int(sys.argv[4])

    # 環境変数からのオーバーライド（Orchestrator との連携用）
    repo_path = os.environ.get("BROWNIE_WORKSPACE_ROOT", repo_path)
    reference_path = os.environ.get("BROWNIE_REFERENCE_ROOT", reference_path)

    repo_path = os.path.realpath(repo_path)
    reference_path = os.path.realpath(reference_path)

    from src.core.sandbox_manager import SandboxManager
    _sandbox = SandboxManager(user_id, group_id)
    _sandbox.set_workspace_root(repo_path)
    _sandbox.set_reference_root(reference_path)

    logger.info(f"Workspace Server initialized: workspace={repo_path}, reference={reference_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _init_from_args()
    mcp.run(transport="stdio")
