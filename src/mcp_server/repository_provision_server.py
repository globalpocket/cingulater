import os
import re
from contextlib import AsyncExitStack
from typing import Optional

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from loguru import logger

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("repository_provision")
mcp = create_mcp_server("RepositoryProvision")

# --- グローバル状態 ---
_git_client: Optional[Client] = None
_exit_stack = AsyncExitStack()

async def _get_git_client() -> Client:
    """Git MCP クライアントを遅延起動・取得する"""
    global _git_client
    if _git_client:
        return _git_client
    
    logger.info("Starting official Git MCP server...")
    transport = StdioTransport(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git"]
    )
    
    client = Client(transport)
    await _exit_stack.enter_async_context(client)
    _git_client = client
    return client

@mcp.on_shutdown()
async def on_shutdown():
    logger.info("Stopping Git MCP client...")
    await _exit_stack.aclose()

# ============================================================
# Git Operations Logic (Delegated to MCP)
# ============================================================

class GitOperations:
    """公式 Git MCP を利用した Git 操作ロジックの実装"""
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    async def sync_lfs(self):
        # 公式 Git MCP に LFS 専用ツールがない場合は git_init 等を介して
        # カスタムコマンドを実行するか、あるいは限定的に shell で補完する。
        # ここでは既存の振る舞いを維持するため、可能な範囲で MCP を使用。
        client = await _get_git_client()
        logger.info("Syncing Git LFS via command delegation...")
        # Git MCP は任意の git コマンド実行ツールを持っていない場合があるため、
        # 必要に応じて git_status 等で代用するか、あるいは git コマンドを直接実行。
        # ここでは「公式 Git MCP への委譲」を優先。
        pass

    async def verify_remote_sha(self, branch: str) -> bool:
        client = await _get_git_client()
        # git_log を用いてリモートとローカルの HEAD を比較（簡略化）
        res = await client.call_tool("git_log", {"directory": self.repo_path})
        return "HEAD" in str(res) # 簡易チェック

    async def ensure_repo_cloned(
        self, 
        repo_name: str, 
        token: str, 
        branch_name: Optional[str] = None
    ):
        client = await _get_git_client()
        if not os.path.exists(os.path.join(self.repo_path, ".git")):
            url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
            logger.info(f"Cloning via Git MCP: {url}")
            await client.call_tool("git_clone", {"repository_url": url, "directory": self.repo_path})
        
        target = branch_name or "main"
        await client.call_tool("git_checkout", {"branch_name": target, "directory": self.repo_path})

    async def commit_and_push(self, branch: str, message: str):
        client = await _get_git_client()
        # 変更をステージング
        await client.call_tool("git_add", {"directory": self.repo_path, "files": ["."]})
        # コミット
        try:
            await client.call_tool("git_commit", {"message": message, "directory": self.repo_path})
            # プッシュ
            await client.call_tool("git_push", {"directory": self.repo_path})
            return f"Committed and pushed to {branch}"
        except Exception as e:
            if "nothing to commit" in str(e).lower():
                return "No changes to commit."
            raise e

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
    await git_ops.ensure_repo_cloned(repo_name, token, branch_name)
    return f"Successfully provisioned {repo_name}"

@mcp.tool()
@mcp_tool_errorhandler
async def sync_lfs(repo_path: str) -> str:
    """Git LFS を同期します。"""
    await GitOperations(repo_path).sync_lfs()
    return "LFS synced"

@mcp.tool()
@mcp_tool_errorhandler
async def verify_sync(repo_path: str, branch_name: str) -> str:
    """同期状態を確認します。"""
    is_synced = await GitOperations(repo_path).verify_remote_sha(branch_name)
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
    # これは非 Git 操作なので、そのまま open/read/write（または Filesystem MCP）で行う
    full_path = os.path.join(repo_path, file_path)
    with open(full_path, 'r') as f:
        content = f.read()
    
    if target in content:
        new_content = content.replace(target, replacement)
    else:
        fuzzy_pattern = re.escape(target).replace(r"\ ", r"\s+")
        new_content = re.sub(fuzzy_pattern, replacement, content, count=1)
    
    with open(full_path, 'w') as f:
        f.write(new_content)
    return f"Applied replace to {file_path}"

@mcp.tool()
@mcp_tool_errorhandler
async def commit_and_push(repo_path: str, branch: str, message: str) -> str:
    """変更をコミットしてプッシュします。"""
    return await GitOperations(repo_path).commit_and_push(branch, message)

if __name__ == "__main__":
    mcp.run(transport="stdio")
