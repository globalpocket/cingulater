import logging
import time
import random
import functools
import json
import asyncio
import requests
import urllib3
import http.client
from typing import Optional, List, Dict, Any
from github import Github, GithubException, Auth
from src.core.persistence import PersistenceManager

logger = logging.getLogger(__name__)


class GitHubRateLimitException(Exception):
    """GitHubのレートリミットに達したことを示す例外"""

    def __init__(self, message: str, reset_at: float):
        super().__init__(message)
        self.reset_at = reset_at


class GitHubConnectionException(Exception):
    """GitHubへの接続エラー（リトライ上限到達）を示す例外"""

    pass


def github_retry(func):
    """GitHub API の一時的なエラーに対するリトライデコレータ"""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        max_retries = 3
        base_delay = 5  # 秒
        for attempt in range(max_retries):
            try:
                # 定期的な強制リフレッシュ (30分以上経過していたらリフレッシュ)
                if (
                    hasattr(self, "_last_refresh_time")
                    and time.time() - self._last_refresh_time > 1800
                ):
                    logger.info("Proactive GitHub client refresh...")
                    self._init_client(self._token)

                return await func(self, *args, **kwargs)
            except (
                GithubException,
                requests.exceptions.ConnectionError,
                urllib3.exceptions.ProtocolError,
                http.client.RemoteDisconnected,
                ConnectionResetError,
            ) as e:
                is_retryable = False
                is_connection_error = False

                # 接続エラー（RemoteDisconnected や ProtocolError）の場合はクライアントを強制リフレッシュ
                if isinstance(
                    e,
                    (
                        requests.exceptions.ConnectionError,
                        urllib3.exceptions.ProtocolError,
                        http.client.RemoteDisconnected,
                        ConnectionResetError,
                    ),
                ):
                    logger.warning(
                        f"Connection error detected ({type(e).__name__}). Forcing GitHub client refresh..."
                    )
                    self._init_client(self._token)
                    is_retryable = True
                    is_connection_error = True
                else:
                    # 429 (Too Many Requests) または 403 (Secondary Rate Limit) はリトライ可能
                    status = getattr(e, "status", None)
                    is_retryable = (status == 429) or (
                        status == 403 and "secondary" in str(e).lower()
                    )

                if is_retryable and attempt < max_retries - 1:
                    delay = (base_delay ** (attempt + 1)) + (random.random() * 5)
                    logger.warning(
                        f"Retrying GitHub API call in {delay:.2f}s... (Attempt {attempt+1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                if is_connection_error:
                    raise GitHubConnectionException(
                        f"Persistent connection failure after {max_retries} attempts: {e}"
                    )

                if isinstance(e, GithubException):
                    self._handle_exception(e)
                else:
                    raise e
        return None

    return wrapper


class GitHubClientWrapper:
    def __init__(self, token: str, persistence: Optional[PersistenceManager] = None):
        if not token:
            raise ValueError("GITHUB_TOKEN is not set.")
        self._token = token
        self._my_username: Optional[str] = None
        self.persistence = persistence
        self.last_api_call_time = 0
        self._init_client(token)

    def _init_client(self, token: str):
        """Githubクライアントの初期化 (設計書 1.2: 接続安定性の確保)"""
        # 既存のクライアントがある場合、明示的に閉じてコネクションプールを破棄する
        if hasattr(self, "g") and self.g:
            try:
                self.g.close()
                logger.debug("Closed existing GitHub client session.")
            except Exception:
                pass

        self.auth = Auth.Token(token)

        # urllib3 のリトライ設定を細かく調整 (Keep-alive 起因の切断対策)
        from urllib3.util import Retry

        retry_config = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )

        self.g = Github(
            auth=self.auth,
            timeout=60,
            user_agent="Brownie/1.0 (globalpocket)",
            retry=retry_config,
        )
        self._last_refresh_time = time.time()
        logger.info("GitHub API client initialized with robust connection pool.")

    async def _throttle(self, is_write: bool = False):
        """API呼び出しの流量を制御する"""
        now = time.time()
        elapsed = now - self.last_api_call_time
        delay = 3.0 if is_write else 1.0
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self.last_api_call_time = time.time()

    def _handle_exception(self, e: GithubException):
        """GitHub例外の共通処理"""
        if e.status == 403 and "rate limit" in str(e).lower():
            reset_at = time.time() + 3600
            if e.headers and "x-ratelimit-reset" in e.headers:
                reset_at = float(e.headers["x-ratelimit-reset"])
            raise GitHubRateLimitException(f"GitHub Rate Limit Reached: {e}", reset_at)
        raise e

    def _get_reactions_summary(self, gh_object) -> str:
        """リアクションのサマリーを取得してJSON文字列にする (同期処理)"""
        try:
            reactions = gh_object.get_reactions()
            summary = {}
            for r in reactions:
                content = r.content
                summary[content] = summary.get(content, 0) + 1
            return json.dumps(summary)
        except Exception:
            return "{}"

    def get_my_username(self) -> str:
        """認証されたユーザーのユーザー名を同期的に取得する"""
        if self._my_username is None:
            try:
                user = self.g.get_user()
                self._my_username = user.login
                logger.info(f"Authenticated as GitHub user: {self._my_username}")
            except Exception as e:
                logger.error(f"Failed to get username: {e}")
                return "unknown"
        return self._my_username

    @github_retry
    async def get_my_username_async(self) -> str:
        """非同期コンテキストからユーザー名を取得する"""
        return await asyncio.to_thread(self.get_my_username)

    @github_retry
    async def get_all_accessible_repositories(self) -> List[str]:
        """リポジトリ名のリストを取得する"""
        try:
            await self._throttle(is_write=False)

            def _fetch():
                repos = self.g.get_user().get_repos(sort="updated", direction="desc")
                return [repo.full_name for i, repo in enumerate(repos) if i < 100]

            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(f"Failed to get repositories: {e}")
            return []

    @github_retry
    async def get_repo_owner(self, repo_name: str) -> str:
        """リポジトリのオーナーを取得する"""
        try:
            await self._throttle(is_write=False)

            def _fetch():
                return self.g.get_repo(repo_name).owner.login

            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(f"Failed to get repo owner: {e}")
            return ""

    @github_retry
    async def get_issues_to_process(self, repo_name: str) -> List[Any]:
        """アサインされたIssueを取得する"""
        try:
            await self._throttle(is_write=False)
            username = await self.get_my_username_async()

            def _fetch():
                repo = self.g.get_repo(repo_name)
                issues = repo.get_issues(
                    state="open", assignee=username, sort="updated", direction="desc"
                )
                return [i for i in issues if getattr(i.user, "type", "") != "Bot"][:50]

            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(f"Failed to get issues: {e}")
            return []

    @github_retry
    async def post_comment(self, repo_name: str, issue_number: int, body: str):
        """コメントを投稿する"""
        try:
            await self._throttle(is_write=True)

            def _post():
                self.g.get_repo(repo_name).get_issue(issue_number).create_comment(body)

            await asyncio.to_thread(_post)
            logger.info(f"Comment posted to {repo_name}#{issue_number}")
        except Exception as e:
            logger.error(f"Failed to post comment: {e}")
            raise

    @github_retry
    async def create_pull_request(
        self, repo_name: str, title: str, body: str, head: str, base: str
    ):
        """PRを作成する"""
        try:
            await self._throttle(is_write=True)

            def _create():
                repo = self.g.get_repo(repo_name)
                try:
                    return repo.create_pull(
                        title=title, body=body, head=head, base=base
                    )
                except GithubException as e:
                    if e.status == 422:
                        pulls = repo.get_pulls(
                            state="open", head=f"{repo.owner.login}:{head}"
                        )
                        if pulls.totalCount > 0:
                            return pulls[0]
                    raise e

            return await asyncio.to_thread(_create)
        except Exception as e:
            logger.error(f"Failed to create PR: {e}")
            return None

    @github_retry
    async def get_comment_body(
        self, repo_name: str, issue_number: int, comment_id: str
    ) -> Optional[str]:
        """コメント本文を取得する"""
        try:
            await self._throttle(is_write=False)

            def _fetch():
                repo = self.g.get_repo(repo_name)
                issue = repo.get_issue(issue_number)
                if comment_id == "body":
                    return issue.body
                if comment_id.startswith("review-"):
                    rev_id = int(comment_id.split("-")[1])
                    for r in issue.as_pull_request().get_reviews():
                        if r.id == rev_id:
                            return r.body
                elif comment_id.startswith("rc-"):
                    rc_id = int(comment_id.split("-")[1])
                    return repo.get_pull_review_comment(rc_id).body
                else:
                    return issue.get_comment(int(comment_id)).body
                return None

            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(f"Failed to get comment: {e}")
            return None

    @github_retry
    async def get_mentions_to_process(
        self, repo_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """通知APIを使用してメンションを取得する (並列高速版)"""
        try:
            import os

            my_username = os.getenv("USER_NAME") or await self.get_my_username_async()

            def _fetch_notifications():
                notifs = self.g.get_user().get_notifications(
                    all=True, participating=True
                )
                return [n for i, n in enumerate(notifs) if i < 50]

            notifications = await asyncio.to_thread(_fetch_notifications)

            results = []
            for n in notifications:
                if n.subject.type not in ["Issue", "PullRequest"]:
                    continue
                issue_repo_name = n.repository.full_name
                if self.persistence:
                    self.persistence.upsert_repository(issue_repo_name)
                if repo_name and issue_repo_name != repo_name:
                    continue

                # 逐次的に処理を実行 (並列処理の禁止)
                try:
                    res = await self._process_single_notification(n, my_username)
                    results.append(res)
                except Exception as e:
                    logger.warning(f"Notification processing failed (sequential): {e}")

            final_mentions = []
            seen_issues = set()
            for r in results:
                if isinstance(r, dict) and r:
                    key = (r["repo_name"], r["number"])
                    if key not in seen_issues:
                        final_mentions.append(r)
                        seen_issues.add(key)
                elif isinstance(r, Exception):
                    logger.warning(f"Notification processing sub-task failed: {r}")

            return final_mentions

        except Exception as e:
            logger.error(f"Error fetching notifications: {e}", exc_info=True)
            return []

    async def _process_single_notification(
        self, n, my_username: str
    ) -> Optional[Dict[str, Any]]:
        """単一の通知を解析して最新のメンション情報を抽出する"""
        try:
            url_parts = n.subject.url.split("/")
            if not url_parts or not url_parts[-1].isdigit():
                return None
            issue_number = int(url_parts[-1])
            repo = n.repository

            # Issue 情報を取得
            issue = await asyncio.to_thread(repo.get_issue, issue_number)
            issue_author = getattr(issue.user, "login", None) if issue.user else None

            latest_mention = None

            # Issue 本文のチェック
            if (
                f"@{my_username.lower()}" in (issue.body or "").lower()
                and issue_author != my_username
            ):
                latest_mention = {
                    "repo_name": repo.full_name,
                    "number": issue.number,
                    "comment_id": f"body_{issue.number}",
                    "body": issue.body,
                    "_created_dt": issue.created_at,
                    "created_at": (
                        issue.created_at.isoformat() if issue.created_at else None
                    ),
                    "updated_at": (
                        issue.updated_at.isoformat()
                        if issue.updated_at
                        else issue.created_at.isoformat()
                    ),
                    "node_id": getattr(issue, "node_id", None),
                    "url": issue.url,
                    "html_url": issue.html_url,
                    "user_login": issue_author,
                    "author_association": getattr(issue, "author_association", None),
                }

            # コメントのチェック (最新 30 件程度で十分)
            def _get_recent_comments():
                return list(issue.get_comments().get_page(0))[:30]

            comments = await asyncio.to_thread(_get_recent_comments)
            for comment in comments:
                c_author = (
                    getattr(comment.user, "login", "").lower() if comment.user else ""
                )
                if (
                    f"@{my_username.lower()}" in (comment.body or "").lower()
                    and c_author != my_username.lower()
                ):
                    if (
                        not latest_mention
                        or comment.created_at > latest_mention["_created_dt"]
                    ):
                        latest_mention = {
                            "repo_name": repo.full_name,
                            "number": issue.number,
                            "comment_id": str(comment.id),
                            "body": comment.body,
                            "_created_dt": comment.created_at,
                            "created_at": (
                                comment.created_at.isoformat()
                                if comment.created_at
                                else None
                            ),
                            "updated_at": (
                                comment.updated_at.isoformat()
                                if comment.updated_at
                                else comment.created_at.isoformat()
                            ),
                            "node_id": getattr(comment, "node_id", None),
                            "url": comment.url,
                            "html_url": comment.html_url,
                            "user_login": getattr(comment.user, "login", None),
                            "author_association": getattr(
                                comment, "author_association", None
                            ),
                        }
            return latest_mention
        except Exception as e:
            logger.debug(f"Failed to process notification for {n.subject.title}: {e}")
            return None

    @github_retry
    async def search_mentions_in_repo(self, repo_name: str) -> List[Dict[str, Any]]:
        """Search APIを使用してメンションを検索する (非ブロッキング)"""
        try:
            my_username = await self.get_my_username_async()
            query = f"repo:{repo_name} mentions:{my_username} is:open"

            def _search():
                items = self.g.search_issues(query=query, sort="updated", order="desc")
                return list(items[:10])

            issues_list = await asyncio.to_thread(_search)
            found = []
            for issue in issues_list:
                try:
                    latest_mention = None
                    issue_author = (
                        getattr(issue.user, "login", None) if issue.user else None
                    )
                    if (
                        f"@{my_username.lower()}" in (issue.body or "").lower()
                        and issue_author != my_username
                    ):
                        latest_mention = {
                            "repo_name": repo_name,
                            "number": issue.number,
                            "comment_id": "body",
                            "body": issue.body,
                            "_created_dt": issue.created_at,
                            "created_at": issue.created_at.isoformat(),
                            "updated_at": issue.updated_at.isoformat(),
                            "node_id": getattr(issue, "node_id", None),
                            "url": issue.url,
                            "html_url": issue.html_url,
                            "user_login": issue_author,
                            "reactions": await asyncio.to_thread(
                                self._get_reactions_summary, issue
                            ),
                        }

                    comments = await asyncio.to_thread(
                        lambda: list(issue.get_comments())
                    )
                    for c in comments:
                        c_author = (
                            getattr(c.user, "login", "").lower() if c.user else ""
                        )
                        if (
                            f"@{my_username.lower()}" in (c.body or "").lower()
                            and c_author != my_username.lower()
                        ):
                            if (
                                not latest_mention
                                or c.created_at > latest_mention["_created_dt"]
                            ):
                                latest_mention = {
                                    "repo_name": repo_name,
                                    "number": issue.number,
                                    "comment_id": str(c.id),
                                    "body": c.body,
                                    "_created_dt": c.created_at,
                                    "created_at": c.created_at.isoformat(),
                                    "updated_at": c.updated_at.isoformat(),
                                    "node_id": getattr(c, "node_id", None),
                                    "url": c.url,
                                    "html_url": c.html_url,
                                    "user_login": getattr(c.user, "login", None),
                                    "reactions": await asyncio.to_thread(
                                        self._get_reactions_summary, c
                                    ),
                                }
                    if latest_mention:
                        found.append(latest_mention)
                except Exception:
                    continue
            return found
        except Exception:
            return []

    @github_retry
    async def get_issue(self, repo_name: str, issue_number: int) -> Dict[str, Any]:
        """Issueの詳細を取得する"""

        def _fetch():
            i = self.g.get_repo(repo_name).get_issue(issue_number)
            return {"title": i.title, "body": i.body, "state": i.state}

        return await asyncio.to_thread(_fetch)

    async def mark_issue_notifications_as_read(self, repo_name: str, issue_number: int):
        """通知を既読にする"""

        def _mark():
            for n in self.g.get_user().get_notifications(participating=True):
                if n.repository.full_name == repo_name and n.subject.url.endswith(
                    f"/{issue_number}"
                ):
                    n.mark_as_read()

        await asyncio.to_thread(_mark)

    async def ensure_repo_cloned(
        self, repo_name: str, repo_path: str, branch_name: Optional[str] = None
    ):
        """リポジトリを最新化する (同期的なGit操作を含む)"""
        import subprocess
        import os

        def _sync():
            target = branch_name or self.g.get_repo(repo_name).default_branch
            if not os.path.exists(os.path.join(repo_path, ".git")):
                os.makedirs(repo_path, exist_ok=True)
                token = os.getenv("GITHUB_TOKEN", "")
                url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
                subprocess.run(["git", "clone", url, "."], cwd=repo_path, check=True)
            subprocess.run(["git", "fetch", "origin"], cwd=repo_path, check=True)
            try:
                subprocess.run(["git", "checkout", target], cwd=repo_path, check=True)
            except Exception:
                subprocess.run(
                    ["git", "checkout", "-b", target, f"origin/{target}"],
                    cwd=repo_path,
                    check=True,
                )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{target}"],
                cwd=repo_path,
                check=True,
            )
            subprocess.run(["git", "lfs", "pull"], cwd=repo_path, check=False)

        await asyncio.to_thread(_sync)
