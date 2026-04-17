import os
import asyncio
from typing import Any, Dict, List, Optional
from ghapi.all import GhApi
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger

class GitHubClient:
    """
    ghapi を用いた堅牢な GitHub API クライアント。
    Tenacity によるリトライ処理と GhApi による簡潔な通信を実現。
    """
    def __init__(self, token: Optional[str] = None):
        self._token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        self.api = GhApi(token=self._token)
        self._username: Optional[str] = None

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _call(self, func, *args, **kwargs):
        """同期的な GhApi 呼び出しにリトライを付与"""
        return func(*args, **kwargs)

    async def _async_call(self, func, *args, **kwargs):
        """イベントループをブロックせずに実行"""
        return await asyncio.to_thread(self._call, func, *args, **kwargs)

    async def get_my_username(self) -> str:
        if self._username:
            return self._username
        res = await self._async_call(self.api.users.get_authenticated)
        self._username = res.login
        return self._username

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        return await self._async_call(self.api.issues.get, owner=owner, repo=repo, issue_number=issue_number)

    async def post_comment(self, owner: str, repo: str, issue_number: int, body: str):
        return await self._async_call(self.api.issues.create_comment, owner=owner, repo=repo, issue_number=issue_number, body=body)

    async def create_pull_request(self, owner: str, repo: str, title: str, head: str, base: str, body: str):
        return await self._async_call(self.api.pulls.create, owner=owner, repo=repo, title=title, head=head, base=base, body=body)

    async def search_repositories(self, query: str) -> List[str]:
        # ページネーションを考慮
        all_repos = []
        page = 1
        while True:
            res = await self._async_call(self.api.search.repos, q=query, per_page=100, page=page)
            items = res.get('items', [])
            all_repos.extend([r.full_name for r in items])
            if len(items) < 100:
                break
            page += 1
        return all_repos

    async def list_notifications(self) -> List[Dict[str, Any]]:
        # 通知一覧の取得 (メンション等)
        return await self._async_call(self.api.activity.list_notifications_for_authenticated_user)

    async def get_mentions(self) -> List[Dict[str, Any]]:
        notifications = await self.list_notifications()
        results = []
        for n in notifications:
            if n.reason in ["mention", "author", "assignee"]:
                # n.subject.url から issue_number を抽出する等の処理
                try:
                    num = int(n.subject.url.split("/")[-1])
                    results.append({
                        "repo_name": n.repository.full_name,
                        "number": num,
                        "comment_id": f"notif_{n.id}",
                        "body": n.subject.title,
                        "updated_at": n.updated_at,
                        "html_url": n.subject.url # 実際には詳細取得が必要だが、一旦保持
                    })
                except (ValueError, IndexError):
                    continue
        return results
