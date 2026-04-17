import os
import asyncio
import time
from typing import Any, Dict, List, Optional
from ghapi.all import GhApi
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger

class GitHubRateLimitError(Exception):
    """GitHub API のレート制限に達した際の例外"""
    def __init__(self, reset_time: float):
        self.reset_time = reset_time
        super().__init__(f"GitHub Rate Limit exceeded. Resets at {reset_time}")

class GitHubClient:
    """
    ghapi を用いた、Rate Limit 意識型の堅牢な GitHub API クライアント。
    """
    def __init__(self, token: Optional[str] = None):
        self._token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        self.api = GhApi(token=self._token)
        self._username: Optional[str] = None
        self._last_notif_check: Optional[str] = None # ISO 8601 string for 'since'

    def _handle_response_headers(self):
        """レスポンスヘッダーから Rate Limit 情報を抽出し、必要に応じて例外を投げる"""
        headers = self.api.last_res.headers
        remaining = int(headers.get("X-RateLimit-Remaining", 5000))
        reset_time = int(headers.get("X-RateLimit-Reset", time.time() + 3600))
        
        logger.debug(f"GitHub Rate Limit: {remaining} remaining.")
        
        if remaining < 10:
            logger.warning(f"GitHub Rate Limit critical: {remaining} left. Reset at {reset_time}")
            if remaining == 0:
                raise GitHubRateLimitError(reset_time)

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _call(self, func, *args, **kwargs):
        """同期的な GhApi 呼び出しにリトライと Rate Limit チェックを付与"""
        res = func(*args, **kwargs)
        self._handle_response_headers()
        return res

    async def _async_call(self, func, *args, **kwargs):
        """イベントループをブロックせずに実行"""
        return await asyncio.to_thread(self._call, func, *args, **kwargs)

    async def get_my_username(self) -> str:
        if self._username:
            return self._username
        res = await self._async_call(self.api.users.get_authenticated)
        self._username = res.login
        return self._username

    async def list_notifications(self, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """通知一覧を取得。since 引数で差分取得をサポート"""
        try:
            return await self._async_call(
                self.api.activity.list_notifications_for_authenticated_user,
                since=since,
                all=False # 未読のみ
            )
        except GitHubRateLimitError as e:
            logger.error(f"Cannot list notifications: {e}")
            raise 

    async def get_mentions(self) -> List[Dict[str, Any]]:
        """新規のメンションを取得し、最終取得時刻を更新する"""
        notifications = await self.list_notifications(since=self._last_notif_check)
        
        results = []
        new_last_check = self._last_notif_check
        
        for n in notifications:
            # 最終更新時刻の更新
            if not new_last_check or n.updated_at > new_last_check:
                new_last_check = n.updated_at

            if n.reason in ["mention", "author", "assignee"]:
                try:
                    num = int(n.subject.url.split("/")[-1])
                    results.append({
                        "repo_name": n.repository.full_name,
                        "number": num,
                        "comment_id": f"notif_{n.id}",
                        "body": n.subject.title,
                        "updated_at": n.updated_at,
                        "subject_type": n.subject.type
                    })
                except (ValueError, IndexError):
                    continue
        
        self._last_notif_check = new_last_check
        return results

    async def post_comment(self, owner: str, repo: str, issue_number: int, body: str):
        return await self._async_call(self.api.issues.create_comment, owner=owner, repo=repo, issue_number=issue_number, body=body)

    async def create_pull_request(self, owner: str, repo: str, title: str, head: str, base: str, body: str):
        return await self._async_call(self.api.pulls.create, owner=owner, repo=repo, title=title, head=head, base=base, body=body)

    async def search_repositories(self, query: str) -> List[str]:
        """リポジトリを検索し、フルネームのリストを返す。ページネーション対応。"""
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
