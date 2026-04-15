"""
BROWNIE Repository Provision MCP Server
=======================================
リポジトリの「プロビジョニング（準備・同期）」を MCP プロトコルで公開するサーバー。
stdio トランスポートで Orchestrator のサブプロセスとして動作する。

公開 Tool:
  - provision_repository(repo_name, repo_path, token, branch_name): クローンおよび最新化
  - sync_lfs(repo_path): LFS ファイルの取得
  - verify_sync(repo_path, branch_name): リモートとの同期確認
"""

import logging
import sys
from typing import Optional
from fastmcp import FastMCP
from src.workspace.git_ops import GitOperations

logger = logging.getLogger(__name__)

# --- サーバーインスタンスの生成 ---
mcp = FastMCP("RepositoryProvision")

# ============================================================
# MCP Tool: provision_repository
# ============================================================
@mcp.tool()
async def provision_repository(
    repo_name: str, 
    repo_path: str, 
    token: str, 
    branch_name: Optional[str] = None
) -> str:
    """GitHub からリポジトリをクローンし、指定されたブランチに最新化します。

    Args:
        repo_name: リポジトリのフル名 (例: "owner/repo")
        repo_path: ローカルの保存先パス
        token: GitHub アクセストークン
        branch_name: 対象ブランチ名 (省略時はデフォルトブランチ)
    """
    try:
        git_ops = GitOperations(repo_path)
        git_ops.ensure_repo_cloned(repo_name, token, branch_name)
        return f"Successfully provisioned repository: {repo_name} at {repo_path}"
    except Exception as e:
        logger.error(f"Failed to provision repository {repo_name}: {e}")
        return f"Error: {str(e)}"

# ============================================================
# MCP Tool: sync_lfs
# ============================================================
@mcp.tool()
async def sync_lfs(repo_path: str) -> str:
    """指定されたリポジトリで Git LFS を同期します。

    Args:
        repo_path: ローカルのリポジトリパス
    """
    try:
        git_ops = GitOperations(repo_path)
        git_ops.sync_lfs()
        return f"Successfully synced LFS for {repo_path}"
    except Exception as e:
        logger.error(f"Failed to sync LFS for {repo_path}: {e}")
        return f"Error: {str(e)}"

# ============================================================
# MCP Tool: verify_sync
# ============================================================
@mcp.tool()
async def verify_sync(repo_path: str, branch_name: str) -> str:
    """リモートブランチとローカルの HEAD が一致しているか確認します。

    Args:
        repo_path: ローカルのリポジトリパス
        branch_name: 確認対象のリモートブランチ名
    """
    try:
        git_ops = GitOperations(repo_path)
        is_synced = git_ops.verify_remote_sha(branch_name)
        if is_synced:
            return f"Status: Synced. Local HEAD matches origin/{branch_name}."
        else:
            return f"Status: DESYNCED. Local HEAD does not match origin/{branch_name}."
    except Exception as e:
        logger.error(f"Failed to verify sync for {repo_path}: {e}")
        return f"Error: {str(e)}"

if __name__ == "__main__":
    # ログ設定 (stderr に出力することで MCP クライアント側で適切に処理される)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="stdio")
