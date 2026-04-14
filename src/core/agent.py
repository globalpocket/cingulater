import os
import logging
import asyncio
from typing import Dict, Any, List, Optional, Union, Literal
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.ext.langchain import LangChainToolset

from src.workspace.sandbox import SandboxManager
from src.workspace.context import WorkspaceContext
from src.version import get_footer
from src.llm.robust_model import get_robust_model, wait_for_llm_ready

logger = logging.getLogger(__name__)

class TaskAbortedException(Exception):
    """ユーザーによって Issue がクローズされた場合に投げられる例外"""
    pass

# --- Blueprint 定義 (設計思想: 決定論的な JSON 連携) ---

class BlueprintFile(BaseModel):
    path: str = Field(..., description="修正または作成対象のファイルパス")
    purpose: str = Field(..., description="そのファイルに対する変更の目的")

class Blueprint(BaseModel):
    """
    Planner から Executor へ渡される厳格な設計図。
    Vector通信（文脈の垂れ流し）を廃止し、この構造体のみで指示を完結させます。
    """
    target_files: List[BlueprintFile] = Field(..., description="操作対象ファイルの一覧")
    logic_constraints: List[str] = Field(..., description="実装すべきロジックの制約条件")
    prohibited_actions: List[str] = Field(..., description="禁止事項・変更不可な箇所")
    context_snippets: Optional[List[Dict[str, str]]] = Field(None, description="参考にするコード片 (file, snippet)")

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

# --- Executor エージェント (専門的実行) ---

import os
os.environ.setdefault("OPENAI_API_KEY", "EMPTY")

executor_agent = Agent(
    # 型定義は行わず、指示に従って Markdown を返す
    'openai:dummy', # 実行時に model が上書きされる
    deps_type=AgentDeps,
    system_prompt=(
        "あなたは高度なソフトウェアエンジニア（Executor）です。\n"
        "Planner から渡される「Strict Blueprint（厳密な設計図）」は絶対のルールです。\n"
        "設計図に記載されていない独自の解釈、機能追加、リファクタリングは厳禁です。\n"
        "回答は実装コード案のみとし、ツール呼び出しは一切行わず、純粋な Markdown で返してください。"
    )
)

# --- Planner エージェント ---

planner_agent = Agent(
    'openai:dummy', # 実行時に model が上書きされる
    deps_type=AgentDeps,
    output_type=Union[Blueprint, str], # Blueprint または ユーザーへの回答メッセージ
)

# --- ツールの移植とバインディング ---

@planner_agent.tool
async def post_comment(ctx: RunContext[AgentDeps], body: str) -> str:
    """GitHub の Issue または PR にコメントを投稿します。"""
    await ctx.deps.ensure_open()
    await ctx.deps.gh_client.post_comment(
        ctx.deps.current_repo_name, 
        ctx.deps.current_issue_number, 
        body + get_footer()
    )
    ctx.deps.last_manual_comment = body
    return "Successfully posted comment."

@planner_agent.tool
async def ask_user(ctx: RunContext[AgentDeps], question: str) -> str:
    """ユーザーに質問や確認を求め、回答が得られるまで処理を待機させます。"""
    await ctx.deps.ensure_open()
    ctx.deps.status = "waiting_for_clarification"
    # LangGraph 側でこのステータスを検知して interrupt する
    return "Waiting for user clarification."

@planner_agent.tool
async def finish(ctx: RunContext[AgentDeps], summary: str) -> str:
    """タスクを正常に完了し、最終回答を投稿して終了します。"""
    await ctx.deps.ensure_open()
    ctx.deps.status = "finished"
    return "Task completed."

@planner_agent.tool
async def get_agent_context(ctx: RunContext[AgentDeps]) -> str:
    """エージェントの現在のステータス、カレントディレクトリ、接続されているMCPサーバーの情報を取得します。
    デバッグや「自分が今どこにいるか」を確認するために使用します。
    """
    workspace_root = ctx.deps.workspace_context.root_path if ctx.deps.workspace_context else "Not Set"
    cwd = os.getcwd()
    # MCPServerManager からクライアントの状態を確認
    m = ctx.deps.mcp_manager
    servers = {
        "workspace_server": "Connected" if m.workspace_client else "Disconnected",
        "knowledge_server": "Connected" if m.knowledge_client else "Disconnected",
        "plugins": list(m.plugin_clients.keys())
    }
    return (
        f"Current Context:\n"
        f"- Workspace Root: {workspace_root}\n"
        f"- Current Directory: {cwd}\n"
        f"- Status: {ctx.deps.status}\n"
        f"- MCP Servers: {servers}"
    )

@planner_agent.tool
async def delegate_to_executor(ctx: RunContext[AgentDeps], blueprint: Blueprint) -> str:
    """
    専門家 (Executor) に Blueprint を渡し、具体的なコード実装案の作成を依頼します。
    """
    await ctx.deps.ensure_open()
    logger.info(f"Delegating to Executor with Blueprint for {len(blueprint.target_files)} files.")
    
    # Planner と同じ Deps を共有し、モデルのみ Executor 用のものを使用
    from src.llm.robust_model import get_robust_model
    executor_model_name = ctx.deps.config['llm']['models']['executor']
    executor_endpoint = ctx.deps.config['llm']['executor_endpoint']
    
    # サーバーの準備完了を待機
    await wait_for_llm_ready(executor_endpoint)
    
    executor_model = get_robust_model(executor_model_name, base_url=executor_endpoint)
    
    prompt = f"### STRICT BLUEPRINT ###\n{blueprint.model_dump_json(indent=2)}"
    result = await executor_agent.run(prompt, deps=ctx.deps, model=executor_model)
    return result.data

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
        from src.llm.robust_model import wait_for_llm_ready
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
