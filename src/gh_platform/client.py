import logging
import asyncio
from typing import Optional, List, Dict, Any
from src.core.mcp_server_manager import MCPServerManager
from src.core.persistence import PersistenceManager

logger = logging.getLogger(__name__)

class GitHubRateLimitException(Exception):
    """GitHubのレートリミットに達したことを示す例外"""
    def __init__(self, message: str, reset_at: float):
        super().__init__(message)
        self.reset_at = reset_at

class GitHubClientWrapper:
    """
    GitHub 操作を MCP サーバーに委任するブリッジ。
    直接の API 呼び出し (httpx, PyGithub) を排除する。
    """
    def __init__(self, token: str, mcp_manager: MCPServerManager, persistence: Optional[PersistenceManager] = None):
        self._token = token
        self.mcp_manager = mcp_manager
        self.persistence = persistence
        self._my_username: Optional[str] = None

    async def get_my_username_async(self) -> str:
        """認証されたユーザーのユーザー名を取得する"""
        if self._my_username:
            return self._my_username
        
        client = self.mcp_manager.github_sdk_client
        if not client:
            return "unknown"
            
        try:
            res = await client.call_tool("get_me")
            # FastMCP のレスポンス形式に合わせる (文字列または構造化データ)
            if isinstance(res, dict):
                self._my_username = res.get("login", "unknown")
            else:
                # 文字列などで返ってくる場合のパース (MCP サーバーの実装に依存)
                import json
                try:
                    data = json.loads(res) if isinstance(res, str) else {}
                    self._my_username = data.get("login", "unknown")
                except:
                    self._my_username = str(res)
        except Exception as e:
            logger.error(f"Failed to get username via MCP: {e}")
            return "unknown"
        return self._my_username

    async def get_all_accessible_repositories(self) -> List[str]:
        """アクセス可能なリポジトリ一覧を取得する"""
        client = self.mcp_manager.github_sdk_client
        if not client: return []
        try:
            # 簡略化のため、実際にはページネーションなどが必要
            # ここでは標準サーバーのツールを想定
            res = await client.call_tool("search_repositories", query="user:@me")
            return [repo["full_name"] for repo in res.get("repositories", [])]
        except Exception as e:
            logger.error(f"Failed to list repositories via MCP: {e}")
            return []

    async def post_comment(self, repo_name: str, issue_number: int, body: str):
        """コメントを投稿する"""
        client = self.mcp_manager.github_sdk_client
        if not client: return
        owner, repo = repo_name.split("/")
        try:
            await client.call_tool("add_issue_comment", owner=owner, repo=repo, issue_number=issue_number, body=body)
            logger.info(f"Comment posted to {repo_name}#{issue_number} via MCP")
        except Exception as e:
            logger.error(f"Failed to post comment via MCP: {e}")

    async def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str):
        """PRを作成する"""
        client = self.mcp_manager.github_sdk_client
        if not client: return None
        owner, repo = repo_name.split("/")
        try:
            return await client.call_tool("create_pull_request", owner=owner, repo=repo, title=title, head=head, base=base, body=body)
        except Exception as e:
            logger.error(f"Failed to create PR via MCP: {e}")
            return None

    async def get_mentions_to_process(self, repo_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """通知用 MCP サーバーを使用してメンションを取得する"""
        client = self.mcp_manager.github_notifications_client
        if not client: return []
        
        try:
            # github-notifications-mcp-server のツール名に合わせる
            notifications = await client.call_tool("list-notifications")
            if not notifications: return []
            
            # 結果のフィルタリングとパース (既存ロジックのエッセンスを継承)
            results = []
            my_username = await self.get_my_username_async()
            
            for n in notifications:
                # 簡略化：メンションが含まれる Issue/PR 通知のみを抽出
                # MCP サーバーから返されるデータ構造に依存するが、
                # 概ね既存の _process_single_notification のような変換を行う
                if n.get("reason") in ["mention", "author", "assignee"]:
                    results.append({
                        "repo_name": n["repository"]["full_name"],
                        "number": int(n["subject"]["url"].split("/")[-1]),
                        "comment_id": "notification_" + n["id"],
                        "body": n["subject"]["title"], # 詳細が必要なら別途取得
                        "updated_at": n["updated_at"]
                    })
            return results
        except Exception as e:
            logger.error(f"Failed to get notifications via MCP: {e}")
            return []

    async def get_issue(self, repo_name: str, issue_number: int) -> Dict[str, Any]:
        """Issueの詳細を取得する"""
        client = self.mcp_manager.github_sdk_client
        if not client: return {}
        owner, repo = repo_name.split("/")
        try:
            res = await client.call_tool("issue_read", method="get", owner=owner, repo=repo, issue_number=issue_number)
            return {"title": res.get("title"), "body": res.get("body"), "state": res.get("state")}
        except Exception as e:
            logger.error(f"Failed to get issue via MCP: {e}")
            return {}

    async def mark_issue_notifications_as_read(self, repo_name: str, issue_number: int):
        """通知を既読にする"""
        client = self.mcp_manager.github_notifications_client
        if not client: return
        try:
            # mcollina/github-notifications-mcp-server のツール名に合わせる
            await client.call_tool("mark-thread-read", thread_id=str(notification_id))
        except Exception as e:
            logger.debug(f"Failed to mark notification as read via MCP: {e}")

    async def ensure_repo_cloned(self, repo_name: str, repo_path: str, branch_name: Optional[str] = None):
        """Repository Provision MCP サーバーを使用してリポジトリを最新化する"""
        client = self.mcp_manager.repo_provision_client
        if not client:
            logger.error("Repository Provision Client is not ready.")
            return
            
        try:
            logger.info(f"Delegating repository provision for {repo_name} to MCP...")
            await client.call_tool(
                "provision_repository",
                repo_name=repo_name,
                repo_path=repo_path,
                token=self._token,
                branch_name=branch_name
            )
        except Exception as e:
            logger.error(f"Failed to provision repository via MCP: {e}")
            raise
