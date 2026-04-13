import logging
import time
import random
import functools
from typing import Optional, List, Dict, Any
from github import Github, GithubException, Auth
from src.core.persistence import PersistenceManager
import re
import json
import asyncio
import requests
import urllib3
import http.client

logger = logging.getLogger(__name__)

class GitHubRateLimitException(Exception):
    """GitHubのレートリミットに達したことを示す例外"""
    def __init__(self, message: str, reset_at: float):
        super().__init__(message)
        self.reset_at = reset_at

class GitHubConnectionException(Exception):
    """GitHubへの接続エラー（リトライ上限到達）を示す例外"""
    pass

class GitHubClientWrapper:
    def __init__(self, token: str, persistence: Optional[PersistenceManager] = None):
        if not token:
            raise ValueError("GITHUB_TOKEN is not set. Please set it as an environment variable (e.g., export GITHUB_TOKEN=...).")
        self._token = token
        self._init_client(token)
        self.etags: Dict[str, str] = {}
        self.last_api_call_time = 0
        self._my_username: Optional[str] = None
        self.persistence = persistence

    def _init_client(self, token: str):
        """Githubクライアントの初期化 (コネクションプールのリフレッシュ)"""
        self.auth = Auth.Token(token)
        # User-Agent を設定して GitHub 側での識別を容易にする
        # タイムアウトを 60 秒に延長し、標準のリトライメカニズムを有効化
        self.g = Github(
            auth=self.auth, 
            timeout=60, 
            user_agent="Brownie/1.0 (globalpocket)"
        )
        self._last_refresh_time = time.time()
        logger.info("GitHub API client re-initialized with custom User-Agent and increased timeout.")

    def github_retry(func):
        """GitHub API の一時的なエラーに対するリトライデコレータ"""
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            max_retries = 3
            base_delay = 5  # 秒
            for attempt in range(max_retries):
                try:
                    # 定期的な強制リフレッシュ (30分以上経過していたらリフレッシュ)
                    if time.time() - self._last_refresh_time > 1800:
                        logger.info("Proactive GitHub client refresh...")
                        self._init_client(self._token)
                        
                    return await func(self, *args, **kwargs)
                except (GithubException, requests.exceptions.ConnectionError, urllib3.exceptions.ProtocolError) as e:
                    is_retryable = False
                    is_connection_error = False
                    
                    # 接続エラーの場合はクライアントをリフレッシュ
                    if isinstance(e, (requests.exceptions.ConnectionError, urllib3.exceptions.ProtocolError, http.client.RemoteDisconnected)):
                        logger.warning(f"Connection error detected ({type(e).__name__}). Refreshing GitHub client...")
                        self._init_client(self._token)
                        is_retryable = True
                        is_connection_error = True
                    else:
                        # 429 (Too Many Requests) または 403 (Secondary Rate Limit) はリトライ可能
                        is_retryable = (e.status == 429) or (e.status == 403 and "secondary" in str(e).lower())
                    
                    if is_retryable and attempt < max_retries - 1:
                        # Exponential Backoff + Jitter (揺らぎ)
                        delay = (base_delay ** (attempt + 1)) + (random.random() * 5)
                        logger.warning(f"Retrying GitHub API call in {delay:.2f}s... (Attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    
                    # リトライ上限に達した接続エラーは GitHubConnectionException として送出し、上位でレジューム制御させる
                    if is_connection_error:
                        raise GitHubConnectionException(f"Persistent connection failure after {max_retries} attempts: {e}")
                    
                    if isinstance(e, GithubException):
                        self._handle_exception(e)
                    else:
                        raise e
            return None
        return wrapper

    async def _throttle(self, is_write: bool = False):
        """API呼び出しの流量を制御する (設計書 拡張)"""
        now = time.time()
        elapsed = now - self.last_api_call_time
        # 読み取りは最低1秒、書き込みは最低3秒空ける
        delay = 3.0 if is_write else 1.0
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self.last_api_call_time = time.time()

    def _handle_exception(self, e: GithubException):
        """GitHub例外の共通処理。レートリミットを検知して専用例外を投げる"""
        if e.status == 403 and "rate limit" in str(e).lower():
            # リセット時刻を取得 (デフォルトは1回リトライ後の1時間後)
            reset_at = time.time() + 3600
            if e.headers and 'x-ratelimit-reset' in e.headers:
                reset_at = float(e.headers['x-ratelimit-reset'])
            raise GitHubRateLimitException(f"GitHub Rate Limit Reached: {e}", reset_at)
        raise e

    def _get_reactions_summary(self, gh_object) -> str:
        """リアクションのサマリーを取得してJSON文字列にする"""
        try:
            # PyGithubのget_reactions()を利用してカウントを集計
            reactions = gh_object.get_reactions()
            summary = {}
            for r in reactions:
                content = r.content # "+1", "-1", "laugh", etc.
                summary[content] = summary.get(content, 0) + 1
            return json.dumps(summary)
        except Exception:
            return "{}"

    def get_my_username(self) -> str:
        """認証されたユーザーのユーザー名を動的に取得する"""
        if self._my_username is None:
            try:
                user = self.g.get_user()
                self._my_username = user.login
                logger.info(f"Authenticated as GitHub user: {self._my_username}")
            except GithubException as e:
                self._handle_exception(e)
        return self._my_username

    @github_retry
    async def get_all_accessible_repositories(self) -> List[str]:
        """ユーザーがアクセス可能なすべてのリポジトリ名を動的に取得する（ページネーション対応）"""
        try:
            await self._throttle(is_write=False)
            # 自分が所有、あるいは参加しているリポジトリを取得 (type='all' or 'owner')
            # 負荷軽減のため、まず自分が関わっているものに限定
            repos = self.g.get_user().get_repos(sort='updated', direction='desc')
            
            repo_names = []
            # 最初の100件程度で十分（あまりに多い場合は通知ベースの発見に任せる）
            for i, repo in enumerate(repos):
                if i >= 100: break 
                repo_names.append(repo.full_name)
            
            return repo_names
        except GithubException as e:
            self._handle_exception(e)
            return []

    @github_retry
    async def get_repo_owner(self, repo_name: str) -> str:
        """ リポジトリのオーナー名を取得する """
        try:
            await self._throttle(is_write=False)
            repo = self.g.get_repo(repo_name)
            return repo.owner.login
        except Exception as e:
            logger.error(f"Failed to get repo owner for {repo_name}: {e}")
            return ""

    @github_retry
    async def get_issues_to_process(self, repo_name: str) -> List[Any]:
        """自分（アサイニ）に割り当てられたIssue/PRを取得する。"""
        try:
            await self._throttle(is_write=False)
            my_username = self.get_my_username()
            repo = self.g.get_repo(repo_name)
            
            issues = repo.get_issues(state='open', assignee=my_username, sort='updated', direction='desc')
            
            to_process = []
            for issue in issues:
                if issue.user.type == "Bot":
                    continue
                to_process.append(issue)
            
            if to_process:
                logger.info(f"Found {len(to_process)} issues assigned to {my_username} in {repo_name}")
            return to_process
        except GithubException as e:
            self._handle_exception(e)
            return []

    @github_retry
    async def check_rbac(self, repo_name: str, username: str) -> bool:
        """ユーザーがリポジトリの Collaborator または Owner かを検証する"""
        try:
            await self._throttle(is_write=False)
            repo = self.g.get_repo(repo_name)
            return repo.has_in_collaborators(username)
        except GithubException as e:
            self._handle_exception(e)
            return False

    @github_retry
    async def post_comment(self, repo_name: str, issue_number: int, body: str):
        """コメントを投稿する"""
        try:
            await self._throttle(is_write=True)
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            issue.create_comment(body)
        except GithubException as e:
            logger.info(f"Successfully posted comment to {repo_name}#{issue_number}")
            self._handle_exception(e)

    @github_retry
    async def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str):
        """プルリクエストを作成する (既存の場合は取得する)"""
        try:
            await self._throttle(is_write=True)
            repo = self.g.get_repo(repo_name)
            pr = repo.create_pull(title=title, body=body, head=head, base=base)
            return pr
        except GithubException as e:
            if e.status == 422:
                logger.info(f"PR already exists for {head}. Fetching existing PR...")
                pulls = repo.get_pulls(state='open', head=f"{repo.owner.login}:{head}")
                if pulls.totalCount > 0:
                    return pulls[0]
            self._handle_exception(e)
            return None

    @github_retry
    async def close_pull_request(self, repo_name: str, pull_number: int):
        """プルリクエストを閉じる"""
        try:
            repo = self.g.get_repo(repo_name)
            pr = repo.get_pull(pull_number)
            
            # 関連 Issue の抽出 (タイトルと本文から抽出)
            context_to_scan = f"{pr.title}\n{pr.body or ''}"
            # パターンを強化: Issue #5, Fix #5, または単なる #5 にもマッチさせる
            issue_matches = re.findall(r"(?:Fixes|Closes|Fix|Close|Resolved|Resolves|Issue|See)?\s*#(\d+)", context_to_scan, re.IGNORECASE)
            
            # PR を閉じる
            pr.edit(state="closed")
            logger.info(f"Closed Pull Request #{pull_number} in {repo_name}")
            
            # 関連 Issue への通知
            for issue_num_str in set(issue_matches): # 重複を除去
                issue_num = int(issue_num_str)
                # 自身（PR番号）への通知は避ける
                if issue_num == pull_number:
                    continue
                    
                try:
                    msg = f"📢 この Issue に関連する PR #{pull_number} がキャンセル（クローズ）されました。必要に応じて作業状況を再確認してください。"
                    issue = repo.get_issue(issue_num)
                    issue.create_comment(msg)
                    logger.info(f"Notified Issue #{issue_num} about PR #{pull_number} closure.")
                except Exception as ie:
                    logger.warning(f"Failed to notify linked Issue #{issue_num}: {ie}")
            
            # トピックブランチの削除 (設計書 7.1)
            # 安全のため、主要なブランチは除外
            branch_name = pr.head.ref
            protected_branches = ["main", "master", "develop", "stg", "prod"]
            
            # head と base が同じリポジトリ（自リポジトリ内ブランチ）の場合のみ削除
            if branch_name not in protected_branches and pr.head.repo.full_name == repo_name:
                try:
                    ref = repo.get_git_ref(f"heads/{branch_name}")
                    ref.delete()
                    logger.info(f"Deleted topic branch '{branch_name}' after closing PR #{pull_number}.")
                except GithubException as ge:
                    if ge.status == 404:
                        logger.info(f"Branch '{branch_name}' already deleted or not found.")
                    else:
                        logger.warning(f"Failed to delete branch '{branch_name}': {ge}")
                except Exception as e:
                    logger.warning(f"Unexpected error deleting branch '{branch_name}': {e}")
                    
        except GithubException as e:
            logger.error(f"Failed to close PR #{pull_number}: {e}")

    @github_retry
    async def create_issue(self, repo_name: str, title: str, body: str) -> int:
        """新しいIssueを作成する"""
        try:
            repo = self.g.get_repo(repo_name)
            issue = repo.create_issue(title=title, body=body)
            logger.info(f"Successfully created Issue #{issue.number} in {repo_name}: {title}")
            # レートリミット対策: 書き込み操作の後にスリープを入れる
            time.sleep(2)
            return issue.number
        except GithubException as e:
            logger.error(f"Failed to create issue in {repo_name}: {e}")
            raise

    @github_retry
    async def close_issue(self, repo_name: str, issue_number: int):
        """Issueをクローズする"""
        try:
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            issue.edit(state="closed")
            logger.info(f"Closed Issue #{issue_number} in {repo_name}")
            # レートリミット対策: 書き込み操作の後にスリープを入れる
            time.sleep(2)
        except GithubException as e:
            logger.error(f"Failed to close issue in {repo_name}: {e}")
            raise

    @github_retry
    async def merge_pull_request(self, repo_name: str, pull_number: int, commit_message: str = ""):
        """プルリクエストをマージする"""
        try:
            repo = self.g.get_repo(repo_name)
            pr = repo.get_pull(pull_number)
            pr.merge(commit_message=commit_message)
            logger.info(f"Merged Pull Request #{pull_number} in {repo_name}")
            # レートリミット対策: 書き込み操作の後にスリープを入れる
            time.sleep(2)
        except GithubException as e:
            logger.error(f"Failed to merge PR #{pull_number}: {e}")

    @github_retry
    async def get_inline_comment_context(self, repo_name: str, comment_id: int):
        """インラインコメント（Diffコメント）から対象ファイルと行番号、コード断片を取得する"""
        try:
            repo = self.g.get_repo(repo_name)
            # GitHub API では pull コメントとして取得
            comment = repo.get_pull_review_comment(comment_id)
            return {
                "path": comment.path,
                "line": comment.line or comment.original_line,
                "diff_hunk": comment.diff_hunk,
                "body": comment.body
            }
        except Exception as e:
            logger.error(f"Failed to get inline context for comment {comment_id}: {e}")
            return None

    @github_retry
    async def get_issue_labels(self, repo_name: str, issue_number: int) -> List[str]:
        """Issue のラベル一覧を取得する"""
        try:
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            return [l.name for l in issue.get_labels()]
        except GithubException as e:
            logger.error(f"Failed to get labels: {e}")
            return []

    @github_retry
    async def add_label(self, repo_name: str, issue_number: int, label_name: str):
        """Issue にラベルを付与する"""
        try:
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            # ラベルが存在しない場合に備えて、リポジトリ側での存在確認は省略（PyGithubが良きに計らう）
            issue.add_to_labels(label_name)
            logger.info(f"Added label '{label_name}' to Issue #{issue_number}")
        except GithubException as e:
            logger.error(f"Failed to add label: {e}")

    @github_retry
    async def remove_label(self, repo_name: str, issue_number: int, label_name: str):
        """Issue からラベルを削除する"""
        try:
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            issue.remove_from_labels(label_name)
            logger.info(f"Removed label '{label_name}' from Issue #{issue_number}")
        except GithubException as e:
            # ラベルが元々付いていない場合のエラーは無視
            logger.debug(f"Label '{label_name}' not found on Issue #{issue_number}, skipping remove.")

    @github_retry
    async def ensure_repo_cloned(self, repo_name: str, repo_path: str, branch_name: Optional[str] = None):
        """設計書 7.2: リポジトリをクローンまたは最新状態にする"""
        import subprocess
        import os
        import shutil
        
        target_branch = branch_name
        if not target_branch:
            # PyGithubを利用してリポジトリのデフォルトブランチを動的に取得
            try:
                repo = self.g.get_repo(repo_name)
                target_branch = repo.default_branch
            except Exception as e:
                logger.warning(f"Failed to get default branch for {repo_name}: {e}. Falling back to 'main'.")
                target_branch = "main"

        def do_clone():
            token = os.getenv("GITHUB_TOKEN", "")
            clone_url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
            logger.info(f"Cloning {repo_name} to {repo_path}...")
            os.makedirs(repo_path, exist_ok=True)
            # クローン直後はデフォルトブランチになるが、後続の checkout で指定ブランチに合わせる
            subprocess.run(["git", "clone", clone_url, "."], cwd=repo_path, check=True)

        try:
            if not os.path.exists(os.path.join(repo_path, ".git")):
                do_clone()
            
            # 最新化 (指定されたブランチまたはデフォルトブランチを使用)
            logger.info(f"Syncing {repo_name} in {repo_path} to latest on branch: {target_branch}...")
            # origin から最新を取得
            subprocess.run(["git", "fetch", "origin"], cwd=repo_path, check=True)
            # 指定ブランチに切り替え（ローカルにない場合は origin から作成）
            try:
                subprocess.run(["git", "checkout", target_branch], cwd=repo_path, check=True)
            except subprocess.CalledProcessError:
                # 存在しない場合は origin/{target_branch} から作成を試みる
                subprocess.run(["git", "checkout", "-b", target_branch, f"origin/{target_branch}"], cwd=repo_path, check=True)
            
            # origin の状態に強制リセット（クリーンな最新状態を保証）
            subprocess.run(["git", "reset", "--hard", f"origin/{target_branch}"], cwd=repo_path, check=True)
            # LFS の同期も追加で行う (設計書 3.2, 5.1)
            subprocess.run(["git", "lfs", "pull"], cwd=repo_path, check=False)
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git operation failed for {repo_name} at {repo_path}. Attempting repair by re-cloning. Error: {e}")
            # ディレクトリが破損している可能性があるため、一度削除して再クローン
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            os.makedirs(repo_path, exist_ok=True)
            do_clone()
            # クローン後に再度指定ブランチに合わせる
            if target_branch:
                subprocess.run(["git", "checkout", target_branch], cwd=repo_path, check=False)

    @github_retry
    async def get_last_bot_comment(self, repo_name: str, issue_number: int) -> Optional[str]:
        """GitHub 上での自分（Bot）の最新コメントを取得する"""
        try:
            me = self.g.get_user().login
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            # コメントを逆順に取得するために最新から数件取得（大量にある場合を考慮）
            comments = issue.get_comments()
            # PaginatedList をリスト化して逆順にする（通常は件数が少ないことを期待）
            for comment in sorted(comments, key=lambda x: x.created_at, reverse=True):
                if comment.user.login == me:
                    return comment.body
            return None
        except Exception as e:
            logger.warning(f"Failed to get last bot comment for {repo_name}#{issue_number}: {e}")
            return None

    @github_retry
    async def get_comment_body(self, repo_name: str, issue_number: int, comment_id: str) -> Optional[str]:
        """各種 ID 形式（body, 数値, review-, rc-）からコメント本文を取得する"""
        try:
            await self._throttle(is_write=False)
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            
            if comment_id == "body":
                return issue.body
                
            if comment_id.startswith("review-"):
                review_id = int(comment_id.split("-")[1])
                # PullRequest オブジェクトを取得
                pr = issue.as_pull_request()
                # 特定の Review を ID で直接取得するメソッドがないため、一覧から探す
                for r in pr.get_reviews():
                    if r.id == review_id:
                        return r.body
                return None
                
            if comment_id.startswith("rc-"):
                rc_id = int(comment_id.split("-")[1])
                # レビューコメント（インラインコメント）を取得
                comment = repo.get_pull_review_comment(rc_id)
                return comment.body
                
            # 通常の Issue/PR コメント (数値文字列)
            comment = issue.get_comment(int(comment_id))
            return comment.body
        except Exception as e:
            logger.error(f"Failed to get comment body for {comment_id}: {e}")
            return None

    @github_retry
    async def get_mentions_to_process(self, repo_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """通知 API を使用して @mentions を含む未処理の通知/コメントを取得する。"""
        try:
            import os
            my_username = os.getenv("USER_NAME") or self.get_my_username()
            results = []
            
            # 通知 API を使用してメンション等を補足
            start_time = time.time()
            try:
                notifications = self.g.get_user().get_notifications(all=True, participating=True)
                # 最初のイテレーションで実際に通信が発生するため、ここで件数を確認
                first_batch = []
                for n in notifications:
                    first_batch.append(n)
                    if len(first_batch) >= 10: break
                
                logger.info(f"Notification pull successful. Found at least {len(first_batch)} items in {time.time() - start_time:.2f}s")
                notifications = first_batch + list(notifications) # 連結して戻す
            except Exception as ne:
                logger.warning(f"Notification API failed or timed out after {time.time() - start_time:.2f}s: {ne}")
                notifications = [] # 失敗時は通知リストを空にして次に進む

            count = 0
            max_notifications = 50 # 負荷軽減
            
            if notifications:
                for n in notifications:
                    if count >= max_notifications:
                        break
                    
                    # Issue または PullRequest の通知を対象とする
                    if n.subject.type not in ["Issue", "PullRequest"]:
                        continue
                    
                    issue_repo_name = n.repository.full_name
                    
                    # 見つかったリポジトリを DB に登録（監視対象リストを動的に構築）
                    if self.persistence:
                        self.persistence.upsert_repository(issue_repo_name)

                    if repo_name and issue_repo_name != repo_name:
                        continue
                    
                    try:
                        # subject.url から Issue 番号を抽出
                        issue_number = int(n.subject.url.split("/")[-1])
                        
                        # 既にアサイン済みとして捕捉されている場合はスキップ（重複排除）
                        if any(r["repo_name"] == issue_repo_name and r["number"] == issue_number for r in results):
                            continue

                        repo = n.repository
                        issue = repo.get_issue(issue_number)
                        
                        count += 1
                        latest_mention = None
                        
                        # 以降、メンション詳細の特定（既存ロジック）
                        
                        # 1. まずIssue本文をチェック (自分自身の投稿は除外)
                        issue_author = issue.user.login if issue.user else None
                        if f"@{my_username}" in (issue.body or "") and issue_author != my_username:
                            latest_mention = {
                                "repo_name": issue_repo_name,
                                "number": issue.number,
                                "comment_id": "body",
                                "body": issue.body,
                                "_created_dt": issue.created_at,
                                "created_at": issue.created_at.isoformat() if issue.created_at else None,
                                "updated_at": issue.updated_at.isoformat() if issue.updated_at else issue.created_at.isoformat(),
                                "node_id": getattr(issue, "node_id", None),
                                "url": issue.url,
                                "html_url": issue.html_url,
                                "user_login": issue_author,
                                "author_association": getattr(issue, "author_association", None),
                                "reactions": self._get_reactions_summary(issue)
                            }
                        
                        # 2. 次にすべてのコメントをチェック
                        try:
                            # 全コメントを取得（API負荷はかかるが、検索APIよりは安定）
                            comments = issue.get_comments()
                            for comment in comments:
                                comment_author = comment.user.login.lower() if comment.user else None
                                if f"@{my_username.lower()}" in (comment.body or "").lower() and comment_author != my_username.lower():
                                    if not latest_mention or comment.created_at > latest_mention["_created_dt"]:
                                        latest_mention = {
                                            "repo_name": issue_repo_name,
                                            "number": issue.number,
                                            "comment_id": str(comment.id),
                                            "body": comment.body,
                                            "_created_dt": comment.created_at,
                                            "created_at": comment.created_at.isoformat() if comment.created_at else None,
                                            "updated_at": comment.updated_at.isoformat() if comment.updated_at else comment.created_at.isoformat(),
                                            "node_id": getattr(comment, "node_id", None),
                                            "url": comment.url,
                                            "html_url": comment.html_url,
                                            "user_login": comment.user.login if comment.user else None,
                                            "author_association": getattr(comment, "author_association", None),
                                            "reactions": self._get_reactions_summary(comment)
                                        }
                        except Exception as ce:
                            logger.warning(f"Failed to scan comments for Issue #{issue_number} in {issue_repo_name}: {ce}")

                        # 3. プルリクエストの場合、レビューとレビューコメントもチェック
                        if issue.pull_request:
                            try:
                                pr = issue.as_pull_request()
                                
                                # レビューサマリー
                                reviews = pr.get_reviews()
                                for review in reviews:
                                    review_author = review.user.login.lower() if review.user else None
                                    if review.body and f"@{my_username.lower()}" in review.body.lower() and review_author != my_username.lower():
                                        if not latest_mention or review.submitted_at > latest_mention["_created_dt"]:
                                            latest_mention = {
                                                "repo_name": issue_repo_name,
                                                "number": issue.number,
                                                "comment_id": f"review-{review.id}",
                                                "body": review.body,
                                                "_created_dt": review.submitted_at,
                                                "created_at": review.submitted_at.isoformat() if review.submitted_at else None,
                                                "updated_at": review.submitted_at.isoformat(),
                                                "node_id": getattr(review, "node_id", None),
                                                "url": getattr(review, "url", None), # Review might not have direct url
                                                "html_url": getattr(review, "html_url", None),
                                                "user_login": review.user.login if review.user else None,
                                                "author_association": getattr(review, "author_association", None),
                                                "reactions": self._get_reactions_summary(review)
                                            }

                                # レビューインラインコメント
                                review_comments = pr.get_review_comments()
                                for r_comment in review_comments:
                                    r_author = r_comment.user.login.lower() if r_comment.user else None
                                    if f"@{my_username.lower()}" in (r_comment.body or "").lower() and r_author != my_username.lower():
                                        if not latest_mention or r_comment.created_at > latest_mention["_created_dt"]:
                                            latest_mention = {
                                                "repo_name": issue_repo_name,
                                                "number": issue.number,
                                                "comment_id": f"rc-{r_comment.id}",
                                                "body": r_comment.body,
                                                "_created_dt": r_comment.created_at,
                                                "created_at": r_comment.created_at.isoformat() if r_comment.created_at else None,
                                                "updated_at": r_comment.updated_at.isoformat() if r_comment.updated_at else r_comment.created_at.isoformat(),
                                                "node_id": getattr(r_comment, "node_id", None),
                                                "url": r_comment.url,
                                                "html_url": r_comment.html_url,
                                                "user_login": r_comment.user.login if r_comment.user else None,
                                                "author_association": getattr(r_comment, "author_association", None),
                                                "reactions": self._get_reactions_summary(r_comment)
                                            }
                            except Exception as pe:
                                logger.warning(f"Failed to scan PR details for Issue #{issue_number} in {issue_repo_name}: {pe}")
                        
                        if latest_mention:
                            results.append(latest_mention)
                    except Exception as ie:
                        logger.error(f"Error processing notification for {issue_repo_name}: {ie}")
                        continue
            
            # DB に登録されているリポジトリを Search API で補完スキャン
            if self.persistence:
                watched_repos = self.persistence.get_watched_repositories()
                logger.info(f"Scanning {len(watched_repos)} watched repositories for mentions...")
                for watched_repo in watched_repos:
                    search_results = await self.search_mentions_in_repo(watched_repo)
                    for sm in search_results:
                        # Search API の結果を優先的に追加または更新
                        existing_idx = next((i for i, r in enumerate(results) if r["repo_name"] == sm["repo_name"] and r["number"] == sm["number"]), None)
                        if existing_idx is not None:
                            # 通知 API より Search API の方がコメント ID 等の情報が正確なため更新
                            results[existing_idx] = sm
                            logger.debug(f"Updated mention info from search: {sm['repo_name']}#{sm['number']}")
                        else:
                            results.append(sm)
                            logger.debug(f"Added new mention from search: {sm['repo_name']}#{sm['number']}")
            else:
                logger.warning("Persistence manager not found. Skipping watched repo search.")

            return results
        except Exception as e:
            logger.error(f"Polling error in get_mentions_to_process: {e}")
            return []

    @github_retry
    async def search_mentions_in_repo(self, repo_name: str) -> List[Dict[str, Any]]:
        """Search API を使用して、特定リポジトリ内の自分へのメンションを検索する"""
        try:
            my_username = self.get_my_username()
            # 過去 24 時間以内の更新に絞って負荷を軽減 (または適切に調整)
            query = f"repo:{repo_name} mentions:{my_username} is:open"
            
            logger.info(f"Searching for mentions in {repo_name} with query: {query}")
            issues = self.g.search_issues(query=query, sort="updated", order="desc")
            
            # 最大 5 件程度に制限
            found = []
            for i, issue in enumerate(issues):
                if i >= 5:
                    break
                # Issue 本文またはコメントから最新のメンション箇所を特定
                latest_mention = None
                
                # 1. 本文をチェック
                issue_author = issue.user.login if issue.user else None
                if f"@{my_username.lower()}" in (issue.body or "").lower() and issue_author != my_username:
                    latest_mention = {
                        "repo_name": repo_name,
                        "number": issue.number,
                        "comment_id": "body",
                        "body": issue.body,
                        "_created_dt": issue.created_at,
                        "created_at": issue.created_at.isoformat() if issue.created_at else None,
                        "updated_at": issue.updated_at.isoformat() if issue.updated_at else issue.created_at.isoformat(),
                        "node_id": getattr(issue, "node_id", None),
                        "url": issue.url,
                        "html_url": issue.html_url,
                        "user_login": issue_author,
                        "author_association": getattr(issue, "author_association", None),
                        "reactions": self._get_reactions_summary(issue)
                    }

                # 2. コメントをチェック
                try:
                    comments = issue.get_comments()
                    for comment in comments:
                        comment_author = comment.user.login.lower() if comment.user else None
                        if f"@{my_username.lower()}" in (comment.body or "").lower() and comment_author != my_username.lower():
                            if not latest_mention or comment.created_at > latest_mention["_created_dt"]:
                                latest_mention = {
                                    "repo_name": repo_name,
                                    "number": issue.number,
                                    "comment_id": str(comment.id),
                                    "body": comment.body,
                                    "_created_dt": comment.created_at,
                                    "created_at": comment.created_at.isoformat() if comment.created_at else None,
                                    "updated_at": comment.updated_at.isoformat() if comment.updated_at else comment.created_at.isoformat(),
                                    "node_id": getattr(comment, "node_id", None),
                                    "url": comment.url,
                                    "html_url": comment.html_url,
                                    "user_login": comment.user.login if comment.user else None,
                                    "author_association": getattr(comment, "author_association", None),
                                    "reactions": self._get_reactions_summary(comment)
                                }
                except Exception as ce:
                    logger.warning(f"Failed to fetch comments for search match #{issue.number} in {repo_name}: {ce}")

                if latest_mention:
                    found.append(latest_mention)
                else:
                    # 見つからなかった場合は（検索自体にはヒットしているので）Issue 情報を最小限で返す
                    found.append({
                        "repo_name": repo_name,
                        "number": issue.number,
                        "comment_id": "search-match",
                        "body": issue.body,
                        "created_at": issue.updated_at
                    })
            return found
        except Exception as e:
            logger.warning(f"Search failed for {repo_name}: {e}")
            return []

    @github_retry
    async def get_issue(self, repo_name: str, issue_number: int) -> Dict[str, Any]:
        """Issueの詳細を取得し、辞書形式で返す（Orchestrator向け）"""
        try:
            await self._throttle(is_write=False)
            repo = self.g.get_repo(repo_name)
            issue = repo.get_issue(issue_number)
            return {
                "title": issue.title,
                "body": issue.body,
                "state": issue.state
            }
        except Exception as e:
            logger.error(f"Error in get_issue: {e}")
            return {}

    @github_retry
    async def mark_issue_notifications_as_read(self, repo_name: str, issue_number: int):
        """特定の Issue に関連する通知をすべて既読としてマークする"""
        try:
            await self._throttle(is_write=True)
            # 通知一覧を取得 (全件チェックは重いため、最近の通知に絞る)
            notifications = self.g.get_user().get_notifications(participating=True)
            for n in notifications:
                try:
                    # subject.url が https://api.github.com/repos/owner/repo/issues/number の形式
                    if n.repository.full_name == repo_name and n.subject.url.endswith(f"/{issue_number}"):
                        n.mark_as_read()
                        logger.info(f"Marked notification as read for {repo_name}#{issue_number}")
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to mark notifications as read for {repo_name}#{issue_number}: {e}")
