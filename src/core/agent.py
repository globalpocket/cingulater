import os
import logging
import asyncio
import json
import time
import re
import httpx
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.ext.langchain import LangChainToolset

from src.core.types import Blueprint, BlueprintFile

from src.core.sandbox_manager import SandboxManager, WorkspaceContext
from src.utils.config_loader import get_footer
from src.core.mcp_server_manager import MCPServerManager


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
    def __init__(self, token: str, mcp_manager: MCPServerManager):
        self._token = token
        self.mcp_manager = mcp_manager
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
            if isinstance(res, dict):
                self._my_username = res.get("login", "unknown")
            else:
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
        client = self.mcp_manager.github_sdk_client
        if not client: return []
        try:
            res = await client.call_tool("search_repositories", query="user:@me")
            return [repo["full_name"] for repo in res.get("repositories", [])]
        except Exception as e:
            logger.error(f"Failed to list repositories via MCP: {e}")
            return []

    async def post_comment(self, repo_name: str, issue_number: int, body: str):
        client = self.mcp_manager.github_sdk_client
        if not client: return
        owner, repo = repo_name.split("/")
        try:
            await client.call_tool("add_issue_comment", owner=owner, repo=repo, issue_number=issue_number, body=body)
        except Exception as e:
            logger.error(f"Failed to post comment via MCP: {e}")

    async def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str):
        client = self.mcp_manager.github_sdk_client
        if not client: return None
        owner, repo = repo_name.split("/")
        try:
            return await client.call_tool("create_pull_request", owner=owner, repo=repo, title=title, head=head, base=base, body=body)
        except Exception as e:
            logger.error(f"Failed to create PR via MCP: {e}")
            return None

    async def get_mentions_to_process(self, repo_name: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self.mcp_manager.github_notifications_client
        if not client: return []
        try:
            notifications = await client.call_tool("list-notifications")
            if not notifications: return []
            results = []
            for n in notifications:
                if n.get("reason") in ["mention", "author", "assignee"]:
                    results.append({
                        "repo_name": n["repository"]["full_name"],
                        "number": int(n["subject"]["url"].split("/")[-1]),
                        "comment_id": "notification_" + n["id"],
                        "body": n["subject"]["title"],
                        "updated_at": n["updated_at"]
                    })
            return results
        except Exception as e:
            logger.error(f"Failed to get notifications via MCP: {e}")
            return []

    async def get_issue(self, repo_name: str, issue_number: int) -> Dict[str, Any]:
        client = self.mcp_manager.github_sdk_client
        if not client: return {}
        owner, repo = repo_name.split("/")
        try:
            res = await client.call_tool("issue_read", method="get", owner=owner, repo=repo, issue_number=issue_number)
            return {"title": res.get("title"), "body": res.get("body"), "state": res.get("state")}
        except Exception as e:
            logger.error(f"Failed to get issue via MCP: {e}")
            return {}

    async def ensure_repo_cloned(self, repo_name: str, repo_path: str, branch_name: Optional[str] = None):
        client = self.mcp_manager.repo_provision_client
        if not client: return
        try:
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

logger = logging.getLogger(__name__)

class TaskAbortedException(Exception):
    """ユーザーによって Issue がクローズされた場合に投げられる例外"""
    pass

# Note: Blueprint と BlueprintFile は src.core.types に移動しました。

# --- エージェントの依存関係 (Deps) 定義 ---

class AgentDeps:
    def __init__(self, 
                 config: Dict[str, Any], 
                 sandbox: SandboxManager, 
                 gh_client: Any,
                 mcp_manager: Any,
                 workspace_context: Optional[WorkspaceContext] = None):
        self.config = config
        self.sandbox = sandbox
        self.gh_client = gh_client
        self.mcp_manager = mcp_manager
        self.workspace_context = workspace_context
        self.current_task_id: Optional[str] = None
        self.current_repo_name: Optional[str] = None
        self.current_issue_number: Optional[int] = None
        self.status: str = "running"
        self.last_manual_comment: Optional[str] = None
        self._last_open_check: float = 0

    async def ensure_open(self):
        """
        現在の Issue がまだオープン状態か確認し、クローズされていれば TaskAbortedException を投げる。
        API 負荷軽減のため、前回のチェックから 30 秒以内の場合はキャッシュを利用する。
        """
        import time
        now = time.time()
        if now - self._last_open_check < 30:
            return

        logger.debug(f"Checking if issue {self.current_repo_name}#{self.current_issue_number} is still open...")
        issue = await self.gh_client.get_issue(self.current_repo_name, self.current_issue_number)
        
        if issue.get("state") != "open":
            logger.warning(f"Task aborted: Issue {self.current_repo_name}#{self.current_issue_number} is CLOSED.")
            raise TaskAbortedException(f"Issue {self.current_repo_name}#{self.current_issue_number} was closed by user.")
        
        self._last_open_check = now


# NOTE: executor_agent, planner_agent, および delegate_to_executor は 
# src/mcp_server/code_planner_server.py および code_writer_server.py へ移行されました。

# --- CoderAgent (Facade) ---

class CoderAgent:
    """
    Pydantic AI エージェントを統括するファサードクラス。
    Orchestrator からのリクエストを受け取り、適切なエージェントを起動します。
    """
    def __init__(self, 
                 config: Dict[str, Any], 
                 sandbox: SandboxManager, 
                 gh_client: Any,
                 mcp_manager: Any,
                 workspace_context: Optional[WorkspaceContext] = None):
        self.deps = AgentDeps(config, sandbox, gh_client, mcp_manager, workspace_context)
        
        # 堅牢なモデルの取得
        planner_model_name = config['llm']['models']['planner']
        planner_endpoint = config['llm']['planner_endpoint']
        
        # サーバーの準備完了を待機 (同期的なコンストラクタ内なので、ここでは情報を取得するのみとし)
        # 実際の待機は run メソッドの冒頭で行う
        self.planner_model = get_robust_model(planner_model_name, base_url=planner_endpoint)
        self.planner_endpoint = planner_endpoint
        
        # システムプロンプトの読み込み
        self.system_prompt = self._load_instructions()

    def _load_instructions(self) -> str:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        system_prompt_path = os.path.join(project_root, ".agent", "system_prompt.md")
        common_rules_path = os.path.join(project_root, ".agent", "rules", "common.md")
        
        instructions = []
        if os.path.exists(system_prompt_path):
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                instructions.append(f.read())
        
        instructions.append("\n## Common Rules\n")
        if os.path.exists(common_rules_path):
            with open(common_rules_path, "r", encoding="utf-8") as f:
                instructions.append(f.read())
        
        instructions.append(f"\n## Language Setting\n思考 (thought) およびユーザーへの報告は、原則として {os.getenv('BROWNIE_LANGUAGE', 'Japanese')} で行ってください。\n")
        return "\n".join(instructions)

    async def run(self, task_id: str, repo_name: str, issue_number: int, **kwargs) -> Union[bool, str]:
        """メイン実行ループ"""
        self.deps.current_task_id = task_id
        self.deps.current_repo_name = repo_name
        self.deps.current_issue_number = issue_number
        self.deps.status = "running"
        
        instruction = kwargs.get('task_description', f"Issue #{issue_number} を解決してください。")
        
        # MCP ツールの動的バインド (LangChain MCP Adapters 経由)
        mcp_tools = await self.deps.mcp_manager.get_langchain_tools()
        toolset = LangChainToolset(*mcp_tools)
        
        # 実行前にサーバーの準備完了を待機
        await wait_for_llm_ready(self.planner_endpoint)
        
        logger.info(f"[{task_id}] Pydantic AI Planner starting...")
        
        try:
            # 実行前にステータスを最終確認
            await self.deps.ensure_open()
            
            # 実行
            result = await planner_agent.run(
                instruction, 
                deps=self.deps, 
                model=self.planner_model,
                system_prompt=self.system_prompt,
                toolsets=[toolset]
            )
            
            # 結果の処理
            if self.deps.status == "finished":
                return True
            elif self.deps.status == "waiting_for_clarification":
                return "WAITING"
            
            # 結果が Blueprint の場合は自動的に Executor に投げる（または返却して Workflow で扱う）
            if isinstance(result.data, Blueprint):
                # 明示的にツールを呼ばなかったが Blueprint が返ってきた場合の処理
                logger.info("Planner returned a Blueprint. Executing via delegate tool automatically.")
                # 本来はここで再度エージェントを走らせるか、結果として返す
                return "BLUEPRINT_GENERATED"
                
            return False
        except Exception as e:
            logger.error(f"Pydantic AI Execution Error: {e}", exc_info=True)
            return False

# =============================================================================
# LLM Robustness Layer (統合された robust_model.py の機能)
# =============================================================================

async def robust_response_hook(response: httpx.Response):
    """
    HTTP レスポンスをインターセプトし、規格不備（欠落フィールド）を補完する。
    Local LLM サーバー（MLX 等）が OpenAI 規格のメタデータを返さない場合のバリデーションエラーを防ぐ。
    """
    # 200 OK かつ JSON レスポンスの場合のみ処理
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and "application/json" in content_type:
        try:
            # コンテンツを読み込む
            await response.aread()
            data = response.json()
            modified = False

            # 1. 必須メタデータ id の補完
            if not data.get("id"):
                data["id"] = "chatcmpl-robust-placeholder"
                modified = True

            # 2. object フィールドの補完
            if not data.get("object"):
                data["object"] = "chat.completion"
                modified = True

            # 3. choices フィールド内の index 補完
            if "choices" in data and isinstance(data["choices"], list):
                for i, choice in enumerate(data["choices"]):
                    if "index" not in choice:
                        choice["index"] = i
                        modified = True

            # 4. usage フィールドの補完（深くチェック）
            usage = data.get("usage")
            if not isinstance(usage, dict):
                data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                modified = True
            else:
                for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                    if not isinstance(usage.get(key), int):
                        usage[key] = 0
                        modified = True

            # 5. model フィールドの補完
            if not data.get("model"):
                data["model"] = "robust-model-placeholder"
                modified = True

            # 6. created フィールドの補完
            if not data.get("created"):
                data["created"] = int(time.time())
                modified = True

            # 7. テキストベースのツール呼び出しを構造化データへ変換 (Gemma-4 / MLX 対処)
            if "choices" in data and isinstance(data["choices"], list):
                for choice in data["choices"]:
                    message = choice.get("message", {})
                    content = message.get("content", "")
                    if content and "<|tool_call" in content:
                        # よりアグレッシブな検索: call:name 後のブロックを全て取得
                        match = re.search(r"call:([a-zA-Z0-9_]+)([\{\(].*[\}\)])", content, re.DOTALL)
                        if match:
                            tool_name = match.group(1)
                            tool_args_str = match.group(2).strip()
                            # JSON ヒーリング
                            if "<|\\\"|>" in tool_args_str:
                                tool_args_str = tool_args_str.replace("<|\\\"|>", "\\\"")
                            if "<|\">" in tool_args_str:
                                tool_args_str = tool_args_str.replace("<|\">", "\"")
                            tool_args_str = re.sub(r'([\{\s,])([a-zA-Z0-9_]+):', r'\1"\2":', tool_args_str)
                            
                            tool_call_id = "call_" + data.get("id", "placeholder")[-8:]
                            tool_call = {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_args_str
                                }
                            }
                            if not message.get("tool_calls") or not isinstance(message["tool_calls"], list):
                                message["tool_calls"] = []
                            message["tool_calls"].append(tool_call)
                            message["content"] = "" 
                            modified = True
                            logger.info(f"Converted and healed tool call '{tool_name}' for local LLM.")

            if modified:
                # 修正した JSON でレスポンス内容を書き換える
                response._content = json.dumps(data).encode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to apply robustness fixes to LLM response: {e}")

async def wait_for_llm_ready(endpoint: str, timeout: int = 180):
    """
    LLM サーバーが準備完了になるまで待機する
    """
    if not endpoint:
        return
        
    # localhost を 127.0.0.1 に変換して IPv6 問題を回避
    if "localhost" in endpoint:
        endpoint = endpoint.replace("localhost", "127.0.0.1")
        
    url = f"{endpoint.rstrip('/')}/models"
    logger.info(f"Waiting for LLM server at {url} (timeout: {timeout}s)...")
    
    start_time = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(trust_env=False) as client:
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code == 200:
                    logger.info(f"LLM server at {endpoint} is READY.")
                    return True
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                pass
            except Exception as e:
                logger.debug(f"Wait check failed: {e}")
                
            await asyncio.sleep(5)
            
    logger.error(f"LLM server at {endpoint} failed to become ready within {timeout}s.")
    return False

def get_robust_model(model_name: str, base_url: Optional[str] = None) -> OpenAIModel:
    """
    ローカルサーバーの非標準的な挙動を吸収する設定済みの OpenAIModel を提供する
    """
    # 接続先を 127.0.0.1 に固定して IPv6 競合やプロキシ問題を回避
    if base_url and "localhost" in base_url:
        base_url = base_url.replace("localhost", "127.0.0.1")
        
    logger.info(f"Creating robust model for {model_name} at {base_url}")
    
    # カスタムフックを設定した HTTP クライアントを作成
    http_client = httpx.AsyncClient(
        event_hooks={"response": [robust_response_hook]},
        timeout=httpx.Timeout(120.0, connect=10.0),
        trust_env=False # 環境変数のプロキシ設定を無視
    )
    
    # OpenAI Provider を作成し、カスタム HTTP クライアントを注入
    provider = OpenAIProvider(
        base_url=base_url,
        api_key="EMPTY",
        http_client=http_client
    )
    
    return OpenAIModel(
        model_name,
        provider=provider
    )

