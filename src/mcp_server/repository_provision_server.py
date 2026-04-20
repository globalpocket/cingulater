import os
import re
import time
import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional, Union

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from ghapi.all import GhApi
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging("github_platform")
mcp = create_mcp_server("GitHubPlatform")

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
        command="npx", args=["-y", "@modelcontextprotocol/server-git"]
    )

    client = Client(transport)
    await _exit_stack.enter_async_context(client)
    _git_client = client
    return client

# ============================================================
# GitHub API Client Logic (Integrated from gh_platform_client)
# ============================================================

class GitHubRateLimitError(Exception):
    def __init__(self, reset_time: float):
        self.reset_time = reset_time
        super().__init__(f"GitHub Rate Limit exceeded. Resets at {reset_time}")

class GitHubClient:
    def __init__(self, token: Optional[str] = None):
        self._token = (
            token
            or os.getenv("GITHUB_TOKEN")
            or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        )
        self.api = GhApi(token=self._token)
        self._username: Optional[str] = None

    def _handle_response_headers(self):
        headers = self.api.last_res.headers
        remaining = int(headers.get("X-RateLimit-Remaining", 5000))
        reset_time = int(headers.get("X-RateLimit-Reset", time.time() + 3600))
        if remaining == 0:
            raise GitHubRateLimitError(reset_time)

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _call(self, func, *args, **kwargs):
        res = func(*args, **kwargs)
        self._handle_response_headers()
        return res

    async def _async_call(self, func, *args, **kwargs):
        return await asyncio.to_thread(self._call, func, *args, **kwargs)

# ============================================================
# MCP Tools
# ============================================================

@mcp.tool()
@mcp_tool_errorhandler
async def provision_repository(
    repo_name: str, repo_path: str, token: str, branch_name: Optional[str] = None
) -> str:
    """リポジトリをクローンし、ブランチを最新化します。"""
    if not os.path.exists(os.path.join(repo_path, ".git")):
        url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
        client = await _get_git_client()
        await client.call_tool("git_clone", {"repository_url": url, "directory": repo_path})
    
    # チェックアウト等の追加操作は Git MCP に委譲可能
    return f"Successfully provisioned {repo_name}"

@mcp.tool()
@mcp_tool_errorhandler
async def get_mentions(token: Optional[str] = None) -> List[Dict[str, Any]]:
    """GitHub の自分へのメンション（通知）を取得します。"""
    gh = GitHubClient(token=token)
    notifs = await gh._async_call(gh.api.activity.list_notifications_for_authenticated_user, all=False)
    results = []
    for n in notifs:
        if n.reason in ["mention", "author", "assignee"]:
            num = int(n.subject.url.split("/")[-1])
            results.append({
                "repo_name": n.repository.full_name,
                "number": num,
                "title": n.subject.title,
                "updated_at": n.updated_at,
                "type": n.subject.type
            })
    return results

@mcp.tool()
@mcp_tool_errorhandler
async def post_comment(repo_full_name: str, issue_number: int, body: str, token: Optional[str] = None) -> str:
    """GitHub の Issue または PR にコメントを投稿します。"""
    gh = GitHubClient(token=token)
    owner, repo = repo_full_name.split("/")
    await gh._async_call(gh.api.issues.create_comment, owner=owner, repo=repo, issue_number=issue_number, body=body)
    return "Comment posted successfully"

@mcp.tool()
@mcp_tool_errorhandler
async def get_issue(repo_full_name: str, issue_number: int, token: Optional[str] = None) -> Dict[str, Any]:
    """GitHub の Issue または PR の詳細情報を取得します。"""
    gh = GitHubClient(token=token)
    owner, repo = repo_full_name.split("/")
    res = await gh._async_call(gh.api.issues.get, owner=owner, repo=repo, issue_number=issue_number)
    return {
        "title": res.title,
        "state": res.state,
        "body": res.body,
        "author": res.user.login,
        "created_at": res.created_at,
        "html_url": res.html_url
    }

@mcp.tool()
@mcp_tool_errorhandler
async def create_pull_request(
    repo_full_name: str, title: str, head: str, base: str, body: str, token: Optional[str] = None
) -> str:
    """プルリクエストを作成します。"""
    gh = GitHubClient(token=token)
    owner, repo = repo_full_name.split("/")
    res = await gh._async_call(gh.api.pulls.create, owner=owner, repo=repo, title=title, head=head, base=base, body=body)
    return f"PR created: {res.html_url}"

@mcp.tool()
@mcp_tool_errorhandler
async def commit_and_push(repo_path: str, branch: str, message: str) -> str:
    """変更をコミットしてプッシュします（Git MCP 経由）。"""
    client = await _get_git_client()
    await client.call_tool("git_add", {"directory": repo_path, "files": ["."]})
    await client.call_tool("git_commit", {"message": message, "directory": repo_path})
    await client.call_tool("git_push", {"directory": repo_path})
    return f"Committed and pushed to {branch}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
