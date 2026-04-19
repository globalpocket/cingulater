from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

import yaml
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.core.config import get_settings
from src.utils.llm import get_robust_model

# logger は loguru からインポート済み


def merge_results(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """各ノードの実行結果をマージするためのリデューサー"""
    new_results = a.copy()
    new_results.update(b)
    return new_results


class DynamicWorkflowState(TypedDict):
    """
    動的ワークフローの実行状態を管理する State 定義。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    input_data: Any
    current_status: str
    # results は各ノードの実行結果をマージして保持する
    results: Annotated[Dict[str, Any], merge_results]


class WorkflowNodeDefinition(BaseModel):
    """個別のワークフローノードの定義"""

    prompt_file: str
    next: str
    model: str = "planner"


class WorkflowDefinition(BaseModel):
    """ワークフロー全体の定義"""

    name: str
    description: Optional[str] = None
    start_node: str
    nodes: Dict[str, WorkflowNodeDefinition]
    triggers: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowTool:
    def __init__(
        self,
        name: str,
        source_path: Path,
        scope: str,
        file_type: str,
        definition: Optional[WorkflowDefinition] = None,
        markdown_content: Optional[str] = None,
    ):
        self.name = name
        self.source_path = source_path
        self.scope = scope
        self.file_type = file_type
        self.definition = definition
        self.markdown_content = markdown_content

        if definition:
            self.description = (
                definition.description or f"Workflow tool loaded from {source_path}"
            )
            self.triggers = definition.triggers
        else:
            self.description = f"Markdown tool loaded from {source_path}"
            self.triggers = []


class WorkflowRegistry:
    """ロードされたワークフローを管理し、実行可能な Callable に変換するクラス"""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Optional[Path] = None,
        config_path: Optional[str] = None,
    ):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.settings = get_settings(config_path)

    def set_mcp_tools(self, mcp_tool_names: List[str]):
        self._mcp_tool_names = mcp_tool_names

    def register_tool(
        self, tool: WorkflowTool, content: str, config: Optional[Dict[str, Any]] = None
    ):
        if tool.name in self._mcp_tool_names:
            logger.warning(f"Skipping redundant tool '{tool.name}' (already in MCP).")
            return

        if tool.name in self._tools:
            existing = self._tools[tool.name]
            # 同一スコープ内での重複はスキップ
            if existing.scope == tool.scope:
                if existing.file_type == "yaml" and tool.file_type == "md":
                    return
                logger.warning(
                    f"Skipping redundant tool '{tool.name}' in same scope "
                    f"({tool.scope})."
                )
                return

            # 'workspace' スコープは 'core' をオーバーライドできる
            # (Phase 10: オーバーライド階層)
            if existing.scope == "core" and tool.scope == "workspace":
                logger.info(
                    f"Overriding core tool '{tool.name}' with workspace version."
                )
            else:
                # すでに workspace 版がある場合などはスキップ
                return

        self._tools[tool.name] = tool
        self._callables[tool.name] = self._create_callable(tool, content)
        logger.info(
            f"Registered {tool.file_type} tool '{tool.name}' from {tool.scope} scope."
        )

    def _create_callable(self, tool: WorkflowTool, content: str) -> Callable:

        async def workflow_runner(**kwargs):
            planner_model = self.settings.llm.models.get("planner", "gemma-4-26b-it")
            planner_endpoint = self.settings.llm.planner_endpoint

            if tool.file_type == "yaml":
                wf = tool.definition
                if not wf:
                    return f"Invalid workflow definition: {tool.name}"

                builder = StateGraph(DynamicWorkflowState)

                for node_name, node_cfg in wf.nodes.items():
                    prompt_file_name = node_cfg.prompt_file
                    next_node = node_cfg.next

                    model_name = planner_model
                    endpoint = planner_endpoint
                    if node_cfg.model == "executor":
                        model_name = self.settings.llm.models.get("executor")
                        endpoint = self.settings.llm.executor_endpoint

                    prompt_path = tool.source_path.parent / prompt_file_name

                    builder.add_node(
                        node_name,
                        self._create_node_func(
                            node_name, prompt_path, model_name, endpoint
                        ),
                    )

                    if next_node == "END":
                        builder.set_finish_point(node_name)
                    elif next_node:
                        builder.add_edge(node_name, next_node)

                builder.set_entry_point(wf.start_node)
                graph = builder.compile()

                initial_state = {
                    "messages": [
                        HumanMessage(content=str(kwargs.get("input_data", "")))
                    ],
                    "input_data": kwargs.get("input_data"),
                    "current_status": "starting",
                    "results": {},
                }

                return await graph.ainvoke(initial_state)

            else:
                model = get_robust_model(planner_model, base_url=planner_endpoint)
                agent = Agent(model, system_prompt=tool.markdown_content)
                res = await agent.run(str(kwargs.get("input_data", "")))
                return res.output

        workflow_runner.__name__ = tool.name
        workflow_runner.__doc__ = tool.description
        return workflow_runner


from pydantic_ai import RunContext


class WorkflowDeps(BaseModel):
    """Pydantic-AI の RunContext で利用する依存関係定義"""

    input_data: Any
    results: Dict[str, Any] = Field(default_factory=dict)
    vars: Dict[str, Any] = Field(default_factory=dict)


def _create_node_func(
    self, node_id: str, prompt_path: Path, model_name: str, endpoint: str
):
    """
    特定のノードに対する Pydantic AI Agent 実行関数を生成する。
    RunContext を用いて、Markdown 内の変数を動的に解決する。
    """

    async def node_func(state: DynamicWorkflowState) -> DynamicWorkflowState:
        logger.info(f"--- Node: {node_id} ---")

        # 1. プロンプトの読み込み
        raw_prompt = (
            prompt_path.read_text(encoding="utf-8")
            if prompt_path.exists()
            else f"Prompt file not found: {prompt_path}"
        )

        # 2. モデルとエージェントの準備
        model = get_robust_model(model_name, base_url=endpoint)
        deps = WorkflowDeps(
            input_data=state.get("input_data"),
            results=state.get("results", {}),
            vars=state.get("vars", {}),
        )

        # 3. Pydantic-AI Agent の定義 (動的システムプロンプト & ツール委譲)
        agent = Agent(model, deps_type=WorkflowDeps)

        # 全ての MCP ツールをエージェントに公開する (Phase 7 の核心)
        # ※ 実際にはセキュリティ上、必要最小限にするのが望ましいが、
        # ここでは強力な自律性を確保するため、利用可能な全 MCP ツールをバインドする。
        # Note: Pydantic-AI で外部ツールを動的に追加する仕組みを利用。
        from src.core.orchestrator import global_orchestrator

        if global_orchestrator:
            all_tools = global_orchestrator.mcp_manager.get_all_tools()
            for tool_node in all_tools:
                # ツールをラップして登録 (簡易実装。実際には MCP クライアント経由)
                # ここでは orchestrator の mcp_manager を介して直接呼び出す
                async def mcp_tool_wrapper(ctx: RunContext[WorkflowDeps], **kwargs):
                    # 各ツールの実体は MCPServerManager で管理されている
                    return await global_orchestrator.mcp_manager.call_tool_by_name(
                        tool_node.name, **kwargs
                    )

                mcp_tool_wrapper.__name__ = tool_node.name
                mcp_tool_wrapper.__doc__ = tool_node.description
                agent.tool(mcp_tool_wrapper)

        @agent.system_prompt
        def get_system_prompt(ctx: RunContext[WorkflowDeps]) -> str:
            """Markdown 内の {placeholder} を ctx.deps から解決する"""
            prompt = raw_prompt
            # 展開用コンテキストの作成
            format_ctx = {
                "input_data": ctx.deps.input_data,
                **ctx.deps.results,
                **ctx.deps.vars,
            }
            try:
                # 正規表現による {key} 置換 (Jinja2 等は使わず標準機能で)
                import re

                def replacer(match):
                    key = match.group(1).strip()
                    return str(format_ctx.get(key, match.group(0)))

                return re.sub(r"\{(.+?)\}", replacer, prompt)
            except Exception as e:
                logger.warning(f"Prompt formatting failed: {e}")
                return prompt

        # 4. 実行
        prompt_data = str(state.get("input_data"))
        # ツール呼び出しを許可して実行
        result = await agent.run(prompt_data, deps=deps)

        output = result.output
        state["results"][node_id] = output
        logger.debug(f"Node {node_id} output: {str(output)[:100]}...")
        return state

    return node_func
    def get_tool_dict(self) -> Dict[str, Callable]:
        return self._callables


class WorkflowLoader:
    def __init__(
        self,
        project_root: Path,
        workspace_root: Optional[Path] = None,
        config_path: Optional[str] = None,
    ):
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.registry = WorkflowRegistry(
            project_root, workspace_root, config_path=config_path
        )

    def load_all(self, mcp_tool_names: List[str] = None):
        if mcp_tool_names:
            self.registry.set_mcp_tools(mcp_tool_names)

        core_path = self.project_root / "workflows"
        workspace_path = None
        if self.workspace_root:
            workspace_path = self.workspace_root / ".brwn" / "workflows"

        for pattern in ["**/*.yaml", "**/*.yml", "**/*.md"]:
            self._scan_dir(core_path, "core", pattern)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", pattern)

        return self.registry.get_tool_dict()

    def reload(self, mcp_tool_names: List[str] = None):
        """
        レジストリをクリアしてワークフローを再読み込みします。
        """
        logger.info("Reloading workflows and refreshing registry...")
        self.registry = WorkflowRegistry(
            self.project_root, self.workspace_root, config_path=None
        )  # シングルトンから取得
        return self.load_all(mcp_tool_names=mcp_tool_names)

    def _scan_dir(self, directory: Path, scope: str, pattern: str):
        if not directory.exists() or not directory.is_dir():
            return

        for file_path in directory.glob(pattern):
            if file_path.is_dir():
                continue
            tool_name = file_path.stem
            try:
                content = file_path.read_text(encoding="utf-8")
                file_type = "yaml" if file_path.suffix in [".yaml", ".yml"] else "md"
                definition = None
                markdown_content = None

                if file_type == "yaml":
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        # Workflow 定義に必要なフィールドがあるかチェック
                        if "start_node" in data and "nodes" in data:
                            # Pydantic モデルでバリデーション
                            definition = WorkflowDefinition(**data)
                        else:
                            logger.debug(
                                f"Skipping non-workflow YAML tool: {file_path}"
                            )
                            # 後続の WorkflowTool 作成で definition が
                            # None でも許容される設計であれば続行。
                            # 現状の WorkflowTool は None の場合 MD 扱い。
                else:
                    markdown_content = content

                tool = WorkflowTool(
                    name=tool_name,
                    source_path=file_path,
                    scope=scope,
                    file_type=file_type,
                    definition=definition,
                    markdown_content=markdown_content,
                )
                self.registry.register_tool(tool, content)
            except Exception as e:
                logger.error(f"Failed to load tool from {file_path}: {e}")
