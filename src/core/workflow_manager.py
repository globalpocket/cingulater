from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

import yaml
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

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
        mcp_manager: Optional[Any] = None,
        workspace_root: Optional[Path] = None,
        config_path: Optional[str] = None,
    ):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.mcp_manager = mcp_manager
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
            if existing.scope == tool.scope:
                if existing.file_type == "yaml" and tool.file_type == "md":
                    return
                logger.warning(
                    f"Skipping redundant tool '{tool.name}' "
                    f"in same scope ({tool.scope})."
                )
                return

            if existing.scope == "core" and tool.scope == "workspace":
                logger.info(
                    f"Overriding core tool '{tool.name}' with workspace version."
                )
            else:
                return

        self._tools[tool.name] = tool
        self._callables[tool.name] = self._create_callable(tool, content)
        if tool.definition:
            status = "workflow"
        elif tool.file_type == "md":
            status = "markdown"
        else:
            status = "simple-tool"

        logger.info(
            f"Registered {tool.file_type} {status} '{tool.name}' "
            f"from {tool.scope} scope."
        )

    def _create_callable(self, tool: WorkflowTool, content: str) -> Callable:
        async def workflow_runner(**kwargs):
            orchestrator_model = self.settings.llm.models.get("orchestrator", "gemma-4-26b-it")
            orchestrator_endpoint = self.settings.llm.orchestrator_endpoint

            if tool.file_type == "yaml":
                wf = tool.definition
                if not wf:
                    return f"Invalid workflow definition: {tool.name}"

                builder = StateGraph(DynamicWorkflowState)
                for node_name, node_cfg in wf.nodes.items():
                    model_name = orchestrator_model
                    endpoint = orchestrator_endpoint
                    if node_cfg.model == "executor":
                        model_name = self.settings.llm.models.get("executor")
                        endpoint = self.settings.llm.executor_endpoint

                    prompt_path = tool.source_path.parent / node_cfg.prompt_file
                    builder.add_node(
                        node_name,
                        self._create_node_func(
                            node_name, prompt_path, model_name, endpoint
                        ),
                    )

                    if node_cfg.next == "END":
                        builder.set_finish_point(node_name)
                    elif node_cfg.next:
                        builder.add_edge(node_name, node_cfg.next)

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
                model = get_robust_model(orchestrator_model, base_url=orchestrator_endpoint)
                agent = Agent(model, system_prompt=tool.markdown_content)
                res = await agent.run(str(kwargs.get("input_data", "")))
                return res.output

        workflow_runner.__name__ = tool.name
        workflow_runner.__doc__ = tool.description
        return workflow_runner

    def _create_node_func(
        self, node_id: str, prompt_path: Path, model_name: str, endpoint: str
    ):
        async def node_func(state: DynamicWorkflowState) -> DynamicWorkflowState:
            logger.info(f"--- Node: {node_id} ---")
            raw_prompt = (
                prompt_path.read_text(encoding="utf-8")
                if prompt_path.exists()
                else f"Prompt file not found: {prompt_path}"
            )
            model = get_robust_model(model_name, base_url=endpoint)
            deps = WorkflowDeps(
                input_data=state.get("input_data"),
                results=state.get("results", {}),
                vars=state.get("vars", {}),
            )
            agent = Agent(model, deps_type=WorkflowDeps)

            mcp_manager = self.mcp_manager
            if mcp_manager:
                all_tools = mcp_manager.get_all_tools()
                for tool_node in all_tools:

                    def _make_mcp_tool(node):
                        async def mcp_tool_wrapper(
                            ctx: RunContext[WorkflowDeps], **kwargs
                        ):
                            return await mcp_manager.call_tool_by_name(
                                node.name, **kwargs
                            )

                        mcp_tool_wrapper.__name__ = node.name
                        mcp_tool_wrapper.__doc__ = node.description
                        return mcp_tool_wrapper

                    agent.tool(_make_mcp_tool(tool_node))

            @agent.system_prompt
            def get_system_prompt(ctx: RunContext[WorkflowDeps]) -> str:
                prompt = raw_prompt
                format_ctx = {
                    "input_data": ctx.deps.input_data,
                    **ctx.deps.results,
                    **ctx.deps.vars,
                }
                try:
                    import re

                    def replacer(match):
                        key = match.group(1).strip()
                        return str(format_ctx.get(key, match.group(0)))

                    return re.sub(r"\{(.+?)\}", replacer, prompt)
                except Exception as e:
                    logger.warning(f"Prompt formatting failed: {e}")
                    return prompt

            prompt_data = str(state.get("input_data"))
            result = await agent.run(prompt_data, deps=deps)
            state["results"][node_id] = result.output
            return state

        return node_func

    def get_tool_dict(self) -> Dict[str, Callable]:
        return self._callables


class WorkflowDeps(BaseModel):
    """Pydantic-AI の RunContext で利用する依存関係定義"""

    input_data: Any
    results: Dict[str, Any] = Field(default_factory=dict)
    vars: Dict[str, Any] = Field(default_factory=dict)


class WorkflowLoader:
    def __init__(
        self,
        project_root: Path,
        mcp_manager: Optional[Any] = None,
        workspace_root: Optional[Path] = None,
        config_path: Optional[str] = None,
    ):
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.mcp_manager = mcp_manager
        self.registry = WorkflowRegistry(
            project_root,
            mcp_manager=mcp_manager,
            workspace_root=workspace_root,
            config_path=config_path,
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
        logger.info("Reloading workflows and refreshing registry...")
        self.registry = WorkflowRegistry(
            self.project_root,
            mcp_manager=self.mcp_manager,
            workspace_root=self.workspace_root,
            config_path=None,
        )
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
                        if "start_node" in data and "nodes" in data:
                            # グラフ形式
                            definition = WorkflowDefinition(**data)
                        elif "agent" in data:
                            # 単一エージェント形式 (自動変換)
                            logger.debug(
                                f"Converting single-agent YAML to workflow: {file_path}"
                            )
                            agent_cfg = data["agent"]
                            node_def = WorkflowNodeDefinition(
                                prompt_file=agent_cfg.get(
                                    "system_prompt", f"{tool_name}.md"
                                ),
                                next="END",
                                model="planner",  # デフォルト
                            )
                            definition = WorkflowDefinition(
                                name=data.get("name", tool_name),
                                description=data.get("description"),
                                start_node="main",
                                nodes={"main": node_def},
                            )
                        else:
                            logger.debug(f"Skipping non-workflow YAML: {file_path}")
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
