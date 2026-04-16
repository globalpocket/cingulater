import os
import re
from typing import Optional

import git

from .base_server import create_mcp_server, mcp_tool_errorhandler

mcp = create_mcp_server("RepositoryProvision")

# ============================================================
# Git Operations Logic (Internal)
# ============================================================

class GitOperations:
    """GitPython を利用した Git 操作ロジックの実装"""
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self._repo: Optional[git.Repo] = None

    @property
    def repo(self) -> git.Repo:
        if self._repo is None:
            self._repo = git.Repo(self.repo_path)
        return self._repo

    def sync_lfs(self):
        logger.info("Syncing Git LFS...")
        try:
            # GitPython の git オブジェクト経由でコマンド実行
            self.repo.git.lfs("install")
            self.repo.git.lfs("pull")
        except Exception as e:
            logger.warning(f"LFS sync failed (might not be installed): {e}")

    def verify_remote_sha(self, branch: str) -> bool:
        local_sha = self.repo.head.commit.hexsha
        # リモート情報の取得
        self.repo.remotes.origin.fetch()
        remote_sha = self.repo.remotes.origin.refs[branch].commit.hexsha
        return local_sha == remote_sha

    def ensure_repo_cloned(
        self, 
        repo_name: str, 
        token: str, 
        branch_name: Optional[str] = None
    ):
        if not os.path.exists(os.path.join(self.repo_path, ".git")):
            logger.info(f"Cloning repository: {repo_name} into {self.repo_path}")
            os.makedirs(self.repo_path, exist_ok=True)
            url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
            git.Repo.clone_from(url, self.repo_path)
        
        repo = self.repo
        repo.remotes.origin.fetch()
        
        target = branch_name
        if not target:
            try:
                # デフォルトブランチの取得
                target = repo.git.symbolic_ref(
                    "refs/remotes/origin/HEAD", short=True
                ).split("/")[-1]
            except Exception:
                target = "main"

        logger.info(f"Syncing to branch: {target}")
        
        # チェックアウトとリセット
        repo.git.checkout(target)
        repo.git.reset("--hard", f"origin/{target}")
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
        repo = self.repo
        repo.git.add(A=True)
        if not repo.is_dirty():
            return "No changes to commit."
        
        repo.index.commit(message)
        repo.remotes.origin.push(branch, force=True)
        return f"Committed and pushed to {branch}"

# --- サーバーインスタンスの生成 ---
mcp = create_mcp_server("RepositoryProvision")

# ============================================================
# MCP Tools
# ============================================================

@mcp.tool()
@mcp_tool_errorhandler
async def provision_repository(
    repo_name: str, 
    repo_path: str, 
    token: str, 
    branch_name: Optional[str] = None
) -> str:
    """GitHub からリポジトリをクローンし、ブランチを最新化します。"""
    git_ops = GitOperations(repo_path)
    git_ops.ensure_repo_cloned(repo_name, token, branch_name)
    return f"Successfully provisioned {repo_name}"

@mcp.tool()
@mcp_tool_errorhandler
async def sync_lfs(repo_path: str) -> str:
    """Git LFS を同期します。"""
    GitOperations(repo_path).sync_lfs()
    return "LFS synced"

@mcp.tool()
@mcp_tool_errorhandler
async def verify_sync(repo_path: str, branch_name: str) -> str:
    """同期状態を確認します。"""
    is_synced = GitOperations(repo_path).verify_remote_sha(branch_name)
    return "Synced" if is_synced else "Desynced"

@mcp.tool()
@mcp_tool_errorhandler
async def apply_fuzzy_replace(
    repo_path: str, 
    file_path: str, 
    target: str, 
    replacement: str
) -> str:
    """Fuzzy マッチを利用したテキスト置換を行います。"""
    GitOperations(repo_path).fuzzy_ast_replace(file_path, target, replacement)
    return f"Applied replace to {file_path}"

@mcp.tool()
@mcp_tool_errorhandler
async def commit_and_push(repo_path: str, branch: str, message: str) -> str:
    """変更をコミットしてプッシュします。"""
    return GitOperations(repo_path).commit_and_push(branch, message)

if __name__ == "__main__":
    mcp.run(transport="stdio")
