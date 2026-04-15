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
import os
import subprocess
import re
from typing import Optional, List, Dict, Any
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ============================================================
# Git Operations Logic (Internal)
# ============================================================

class GitOperations:
    """Git コマンドの実行ロジックの実装"""
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def _run_git(self, args: List[str]) -> str:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Git execution error: {e.stderr}")
            raise Exception(f"Git Error: {e.stderr}")

    def sync_lfs(self):
        logger.info("Syncing Git LFS...")
        self._run_git(["lfs", "install"])
        self._run_git(["lfs", "pull"])

    def verify_remote_sha(self, branch: str) -> bool:
        local_sha = self._run_git(["rev-parse", "HEAD"])
        remote_sha = self._run_git(["rev-parse", f"origin/{branch}"])
        return local_sha == remote_sha

    def ensure_repo_cloned(self, repo_name: str, token: str, branch_name: Optional[str] = None):
        if not os.path.exists(os.path.join(self.repo_path, ".git")):
            logger.info(f"Cloning repository: {repo_name} into {self.repo_path}")
            os.makedirs(self.repo_path, exist_ok=True)
            url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
            subprocess.run(["git", "clone", url, "."], cwd=self.repo_path, check=True)
        
        self._run_git(["fetch", "origin"])
        target = branch_name
        if not target:
            try:
                target = self._run_git(["symbolic-ref", "refs/remotes/origin/HEAD"]).split("/")[-1]
            except:
                target = "main"

        logger.info(f"Syncing to branch: {target}")
        try:
            self._run_git(["checkout", target])
        except:
            self._run_git(["checkout", "-b", target, f"origin/{target}"])
            
        self._run_git(["reset", "--hard", f"origin/{target}"])
        self.sync_lfs()

    def fuzzy_ast_replace(self, file_path: str, target: str, replacement: str):
        full_path = os.path.join(self.repo_path, file_path)
        with open(full_path, 'r') as f:
            content = f.read()
        
        if target in content:
            new_content = content.replace(target, replacement)
        else:
            fuzzy_pattern = re.escape(target).replace(r"\ ", r"\s+")
            new_content = re.sub(fuzzy_pattern, replacement, content, count=1)
        
        with open(full_path, 'w') as f:
            f.write(new_content)

    def commit_and_push(self, branch: str, message: str):
        self._run_git(["add", "."])
        status = self._run_git(["status", "--porcelain"])
        if not status:
            return "No changes to commit."
        self._run_git(["commit", "-m", message])
        self._run_git(["push", "origin", branch, "--force"])
        return f"Committed and pushed to {branch}"

# --- サーバーインスタンスの生成 ---
mcp = FastMCP("RepositoryProvision")

# ============================================================
# MCP Tools
# ============================================================

@mcp.tool()
async def provision_repository(repo_name: str, repo_path: str, token: str, branch_name: Optional[str] = None) -> str:
    """GitHub からリポジトリをクローンし、ブランチを最新化します。"""
    try:
        git_ops = GitOperations(repo_path)
        git_ops.ensure_repo_cloned(repo_name, token, branch_name)
        return f"Successfully provisioned {repo_name}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def sync_lfs(repo_path: str) -> str:
    """Git LFS を同期します。"""
    try:
        GitOperations(repo_path).sync_lfs()
        return "LFS synced"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def verify_sync(repo_path: str, branch_name: str) -> str:
    """同期状態を確認します。"""
    try:
        is_synced = GitOperations(repo_path).verify_remote_sha(branch_name)
        return "Synced" if is_synced else "Desynced"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def apply_fuzzy_replace(repo_path: str, file_path: str, target: str, replacement: str) -> str:
    """Fuzzy マッチを利用したテキスト置換を行います。"""
    try:
        GitOperations(repo_path).fuzzy_ast_replace(file_path, target, replacement)
        return f"Applied replace to {file_path}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def commit_and_push(repo_path: str, branch: str, message: str) -> str:
    """変更をコミットしてプッシュします。"""
    try:
        return GitOperations(repo_path).commit_and_push(branch, message)
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run(transport="stdio")
