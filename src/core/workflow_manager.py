from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

import yaml
from jinja2 import Template
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.core.config import get_settings
from src.utils.llm import get_robust_model

logger = logging.getLogger("brownie.workflow_manager")

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
    triggers: List[str] = Field(default_factory=list)

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
            self.description = definition.description or f"Workflow tool loaded from {source_path}"
            self.triggers = definition.triggers
        else:
            self.description = f"Markdown tool loaded from {source_path}"
            self.triggers = []

class WorkflowRegistry:
    """ロードされたワークフローを管理し、実行可能な Callable に変換するクラス"""

    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None, config_path: Optional[str] = None):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.settings = get_settings(config_path)

    def set_mcp_tools(self, mcp_tool_names: List[str]):
        self._mcp_tool_names = mcp_tool_names

    def register_tool(self, tool: WorkflowTool, content: str, config: Optional[Dict[str, Any]] = None):
        if tool.name in self._mcp_tool_names:
            logger.warning(f"Skipping redundant tool '{tool.name}' (already in MCP).")
            return

        if tool.name in self._tools:
            existing = self._tools[tool.name]
            if existing.file_type == "yaml" and tool.file_type == "md":
                return
            logger.warning(f"Skipping redundant tool '{tool.name}' (higher priority exists).")
            return

        self._tools[tool.name] = tool
        self._callables[tool.name] = self._create_callable(tool, content)
        logger.info(f"Registered {tool.file_type} tool '{tool.name}' from {tool.scope} scope.")

    def _create_callable(
        self, tool: WorkflowTool, content: str
    ) -> Callable:

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
                    
                    def make_node(p_path, m_name, e_point, current_node_name):
                        async def node_func(state: DynamicWorkflowState):
                            p_content = (
                                p_path.read_text(encoding="utf-8")
                                if p_path.exists()
                                else f"Prompt file not found: {p_path}"
                            )
                            template = Template(p_content)
                            prompt = template.render(
                                input_data=state.get("input_data"),
                                results=state.get("results"),
                                history=[m.content for m in state.get("messages", [])]
                            )

                            model = get_robust_model(m_name, base_url=e_point)
                            agent = Agent(model, system_prompt=prompt)
                            
                            res = await agent.run(str(state.get("input_data", "")))
                            output = res.output

                            return {
                                "messages": [AIMessage(content=str(output))],
                                "results": {current_node_name: output},
                                "current_status": f"Completed {current_node_name}"
                            }
                        return node_func

                    builder.add_node(node_name, make_node(prompt_path, model_name, endpoint, node_name))
                    
                    if next_node == "END":
                        builder.set_finish_point(node_name)
                    elif next_node:
                        builder.add_edge(node_name, next_node)

                builder.set_entry_point(wf.start_node)
                graph = builder.compile()

                initial_state = {
                    "messages": [HumanMessage(content=str(kwargs.get("input_data", "")))],
                    "input_data": kwargs.get("input_data"),
                    "current_status": "starting",
                    "results": {}
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

    def get_tool_dict(self) -> Dict[str, Callable]:
        return self._callables

class WorkflowLoader:
    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None, config_path: Optional[str] = None):
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.registry = WorkflowRegistry(project_root, workspace_root, config_path=config_path)

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
        self.registry = WorkflowRegistry(self.project_root, self.workspace_root, config_path=None) # シングルトンから取得
        return self.load_all(mcp_tool_names=mcp_tool_names)

    def _scan_dir(self, directory: Path, scope: str, pattern: str):
        if not directory.exists() or not directory.is_dir():
            return

        for file_path in directory.glob(pattern):
            if file_path.is_dir(): continue
            tool_name = file_path.stem
            try:
                content = file_path.read_text(encoding="utf-8")
                description = None
                triggers = None
                file_type = "yaml" if file_path.suffix in [".yaml", ".yml"] else "md"
                definition = None
                markdown_content = None

                if file_type == "yaml":
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        # Pydantic モデルでバリデーション
                        definition = WorkflowDefinition(**data)
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
