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
from typing import Any, Dict, List, Optional

import yaml as pyyaml
from loguru import logger

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("workspace_server")
mcp = create_mcp_server("BrownieWorkspace")


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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
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
@mcp_tool_errorhandler
async def scan_security(path: str = ".") -> str:
    """Bandit 等によるセキュリティ脆弱性をスキャンします。

    Args:
        path: スキャン対象のパス（デフォルト: カレント）
    """
    sandbox = _get_sandbox()
    return await sandbox.scan_security(path)


# ============================================================
# MCP Tool: create_dynamic_workflow (Meta-Agent 機能)
# ============================================================
@mcp.tool()
@mcp_tool_errorhandler
async def create_dynamic_workflow(
    workflow_name: str,
    description: str,
    steps: List[Dict[str, str]],
    triggers: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """新しい動的ワークフロー（YAML + Markdown）を生成して保存します。
    BROWNIE が自律的に自身の機能を拡張（メタ・エージェント）するために使用します。

    Args:
        workflow_name: ワークフローの一意識別子（スネークケース、例: 'code_review'）
        description: このワークフローが何をするものかの説明
        steps: 各ステップの定義リスト。各要素は 'node_name', 'prompt_content', 'next' を持つ。
        triggers: スケジュール実行等のトリガー定義リスト（例: [{'type': 'cron', 'value': '* * * * *'}])
    """
    sandbox = _get_sandbox()
    
    # 書き出し先ディレクトリ（ワークスペース相対パス）
    wf_dir_rel = ".brwn/workflows"
    
    # 1. ワークフロー YAML の構築
    nodes_def = {}
    for step in steps:
        node_name = step["node_name"]
        prompt_file_name = f"node_{node_name}.md"
        nodes_def[node_name] = {
            "prompt_file": prompt_file_name,
            "next": step.get("next", "END")
        }
        
        # 2. 各ステップの Markdown プロンプトを書き出し
        md_content = step.get("prompt_content", "")
        md_path = f"{wf_dir_rel}/{prompt_file_name}"
        await sandbox.write_file(md_path, md_content)
    
    workflow_def = {
        "description": description,
        "triggers": triggers or [],
        "start_node": steps[0]["node_name"] if steps else "NONE",
        "nodes": nodes_def
    }
    
    # 3. YAML ファイルの書き出し
    yaml_content = pyyaml.dump(workflow_def, allow_unicode=True, sort_keys=False)
    yaml_path = f"{wf_dir_rel}/{workflow_name}.yaml"
    await sandbox.write_file(yaml_path, yaml_content)
    
    logger.info(f"Meta-Agent: Created workflow '{workflow_name}'")
    return f"Successfully created dynamic workflow: {workflow_name} ({yaml_path})"


# ============================================================
# シャットダウン・フック
# ============================================================
@mcp.on_shutdown()
async def on_shutdown():
    """サーバー停止時にサンドボックス（および内蔵 MCP クライアント）を停止"""
    global _sandbox
    if _sandbox:
        logger.info("Stopping Sandbox and internal MCP clients...")
        await _sandbox.stop()
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
    _init_from_args()
    mcp.run(transport="stdio")
