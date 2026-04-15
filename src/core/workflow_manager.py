import logging
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

import yaml
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel
from pydantic_ai import Agent

from src.utils.llm import get_robust_model

logger = logging.getLogger(__name__)


class DynamicWorkflowState(TypedDict):
    """動的ワークフローの実行状態"""

    messages: Annotated[list, add_messages]
    input_data: Dict[str, Any]
    current_status: str
    results: Dict[str, Any]


class WorkflowTool(BaseModel):
    """ワークフローツールのメタデータ"""

    name: str
    source_path: Path
    scope: str  # "core" or "workspace"
    file_type: str  # "yaml" or "md"
    description: Optional[str] = None
    triggers: Optional[List[Any]] = None


class WorkflowRegistry:
    """
    読み込まれたワークフローとプロンプトの読み込み・管理を行うレジストリ。
    """

    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []
        self.project_root = project_root
        self.workspace_root = workspace_root

    def set_mcp_tools(self, mcp_tool_names: List[str]):
        """MCPツールの名前一覧を登録（優先順位チェック用）"""
        self._mcp_tool_names = mcp_tool_names

    def register_tool(self, tool: WorkflowTool, content: str, config: Optional[Dict[str, Any]] = None):
        """ツールを登録し、Callableを作成する。優先順位に基づき重複を排除する。"""
        # 1. MCPツールとの重複チェック
        if tool.name in self._mcp_tool_names:
            logger.warning(
                f"Skipping {tool.file_type} tool '{tool.name}' from {tool.scope} "
                f"because an MCP tool with the same name already exists."
            )
            return

        # 2. 既存の登録済みツール（YAML > MD 優先順位）との重複チェック
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            # YAML は MD より優先される
            if existing.file_type == "yaml" and tool.file_type == "md":
                logger.warning(
                    f"Skipping md tool '{tool.name}' from {tool.scope} "
                    f"because a yaml tool with the same name already exists."
                )
                return
            # 同一拡張子の場合は後勝ち（通常はスコープ順で処理される）
            # ただし、今回はユーザー指示により優先順位が決まっているため、
            # ロード順を工夫して「先勝ち」にする。
            logger.warning(
                f"Skipping {tool.file_type} tool '{tool.name}' from {tool.scope} "
                f"because it is already registered (higher priority)."
            )
            return

        # 登録
        self._tools[tool.name] = tool
        self._callables[tool.name] = self._create_callable(tool, content, config=config)
        logger.info(
            f"Registered {tool.file_type} tool '{tool.name}' from {tool.scope} scope."
        )

    def _create_callable(
        self, tool: WorkflowTool, content: str, config: Optional[Dict[str, Any]] = None
    ) -> Callable:
        """YAML/MD 定義から実行可能な Callable (LangGraph/Pydantic AI) を生成する"""

        async def workflow_runner(**kwargs):
            # モデル設定の取得
            planner_model = "gemma-4-26b-it"
            planner_endpoint = "http://127.0.0.1:8080/v1"
            if config:
                planner_model = config["llm"]["models"]["planner"]
                planner_endpoint = config["llm"]["planner_endpoint"]

            if tool.file_type == "yaml":
                data = yaml.safe_load(content)
                nodes_def = data.get("nodes", {})
                start_node = data.get("start_node")

                if not nodes_def or not start_node:
                    return f"Invalid workflow YAML: {tool.name}"

                builder = StateGraph(DynamicWorkflowState)

                # 各ノードの実行関数を定義
                for node_name, node_cfg in nodes_def.items():
                    prompt_file_name = node_cfg.get("prompt_file")
                    next_node = node_cfg.get("next")
                    # モデルの上書き
                    node_model_role = node_cfg.get("model", "planner")
                    model_name = planner_model
                    endpoint = planner_endpoint
                    if config and node_model_role == "executor":
                        model_name = config["llm"]["models"]["executor"]
                        endpoint = config["llm"]["executor_endpoint"]

                    # プロンプトファイルの絶対パス解決
                    prompt_path = tool.source_path.parent / prompt_file_name
                    if not prompt_path.exists():
                        # ファイルが見つからない場合は警告
                        logger.error(f"Prompt file not found: {prompt_path}")

                    async def make_node_func(p_path, m_name, e_point, n_name):
                        async def node_func(state: DynamicWorkflowState):
                            p_content = (
                                p_path.read_text(encoding="utf-8")
                                if p_path.exists()
                                else f"Prompt file not found: {p_path}"
                            )
                            # Pydantic AI Agent の初期化
                            model = get_robust_model(m_name, base_url=e_point)
                            agent = Agent(model, system_prompt=p_content)

                            # 実行
                            # TODO: ここで実際の入力データを組み立てる
                            res = await agent.run(
                                str(state.get("messages", [])),
                                deps=state.get("input_data"),
                            )

                            # 状態の更新
                            state["results"][n_name] = res.data
                            state["current_status"] = f"Completed node: {n_name}"
                            return state

                        return node_func

                    builder.add_node(
                        node_name,
                        await make_node_func(
                            prompt_path, model_name, endpoint, node_name
                        ),
                    )

                    # エッジの追加
                    if next_node == "END":
                        builder.add_edge(node_name, END)
                    elif next_node in nodes_def:
                        builder.add_edge(node_name, next_node)

                builder.set_entry_point(start_node)
                app = builder.compile()

                # 初期状態の構築
                initial_state = {
                    "messages": [],
                    "input_data": kwargs,
                    "current_status": "Starting workflow",
                    "results": {},
                }

                # 実行
                # TODO: ここでLangGraphのコンパイルとinvokeを実行する
                final_state = await app.ainvoke(initial_state)
                return final_state.get("results")

            else:
                # Markdown プロンプトツールの場合は単発の Agent 実行
                model = get_robust_model(planner_model, base_url=planner_endpoint)
                agent = Agent(model, system_prompt=content)
                res = await agent.run(str(kwargs))
                return res.data

        # メタデータの付与
        workflow_runner.__name__ = tool.name
        workflow_runner.__doc__ = (
            tool.description or f"BROWNIE {tool.file_type} tool: {tool.name}"
        )

        # 追加属性の付与 (triggers 等)
        if tool.triggers:
            setattr(workflow_runner, "triggers", tool.triggers)

        return workflow_runner

    def get_tool_dict(self) -> Dict[str, Callable]:
        """登録されたツールの Callable 辞書を返す"""
        return self._callables


class WorkflowLoader:
    """
    Core スコープおよび Workspace スコープからワークフロー/プロンプトを走査する。
    """

    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None):
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.registry = WorkflowRegistry(project_root, workspace_root)

    def load_all(self, mcp_tool_names: List[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        全てのスコープからツールをロードする。
        1. MCPツール初期化
        2. YAMLロード（Core -> Workspace）
        3. Markdownロード（Core -> Workspace）
        """
        if mcp_tool_names:
            self.registry.set_mcp_tools(mcp_tool_names)

        # 優先順位: MCP > YAML > MD
        # ロード順を工夫することで先勝ち（重複排除）を実現する

        # Core スコープの定義: {project_root}/workflows/
        core_path = self.project_root / "workflows"

        # Workspace スコープの定義: {workspace_root}/.brwn/workflows/
        workspace_path = None
        if self.workspace_root:
            workspace_path = self.workspace_root / ".brwn" / "workflows"

        # 解析順序: YAML -> MD
        for ext in ["*.yaml", "*.yml"]:
            self._scan_dir(core_path, "core", ext, config=config)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", ext, config=config)

        for ext in ["*.md"]:
            self._scan_dir(core_path, "core", ext, config=config)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", ext, config=config)

        return self.registry.get_tool_dict()

    def _scan_dir(self, directory: Path, scope: str, pattern: str, config: Optional[Dict[str, Any]] = None):
        """ディレクトリを走査し、ファイルをロードする"""
        if not directory.exists() or not directory.is_dir():
            return

        for file_path in directory.glob(pattern):
            tool_name = file_path.stem
            try:
                content = file_path.read_text(encoding="utf-8")

                # 再帰チェック
                if tool_name in content:
                    raise ValueError(
                        f"Circular reference detected in tool '{tool_name}': "
                        "file contains its own name."
                    )

                # メタデータのパース (YAMLのみ)
                description = None
                triggers = None
                file_type = "yaml" if file_path.suffix in [".yaml", ".yml"] else "md"

                if file_type == "yaml":
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        description = data.get("description")
                        triggers = data.get("triggers")
                        if triggers and not isinstance(triggers, list):
                            triggers = [str(triggers)]

                tool = WorkflowTool(
                    name=tool_name,
                    source_path=file_path,
                    scope=scope,
                    file_type=file_type,
                    description=description,
                    triggers=triggers,
                )

                self.registry.register_tool(tool, content, config=config)

            except Exception as e:
                if isinstance(e, ValueError):
                    raise e
                logger.error(f"Failed to load tool from {file_path}: {e}")
