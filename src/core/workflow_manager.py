import os
import yaml
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Annotated, TypedDict
from jinja2 import Template

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, BaseMessage, AIMessage
from langgraph.graph.message import add_messages
from pydantic_ai import Agent
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

class WorkflowTool:
    def __init__(
        self,
        name: str,
        source_path: Path,
        scope: str,
        file_type: str,
        description: str = None,
        triggers: List[str] = None,
    ):
        self.name = name
        self.source_path = source_path
        self.scope = scope
        self.file_type = file_type
        self.description = description or f"Workflow tool loaded from {source_path}"
        self.triggers = triggers or []

class WorkflowRegistry:
    """ロードされたワークフローを管理し、実行可能な Callable に変換するクラス"""

    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []
        self.project_root = project_root
        self.workspace_root = workspace_root

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
        self._callables[tool.name] = self._create_callable(tool, content, config=config)
        logger.info(f"Registered {tool.file_type} tool '{tool.name}' from {tool.scope} scope.")

    def _create_callable(
        self, tool: WorkflowTool, content: str, config: Optional[Dict[str, Any]] = None
    ) -> Callable:

        async def workflow_runner(**kwargs):
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

                for node_name, node_cfg in nodes_def.items():
                    prompt_file_name = node_cfg.get("prompt_file")
                    next_node = node_cfg.get("next")
                    
                    model_name = planner_model
                    endpoint = planner_endpoint
                    if config and node_cfg.get("model") == "executor":
                        model_name = config["llm"]["models"]["executor"]
                        endpoint = config["llm"]["executor_endpoint"]

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

                builder.set_entry_point(start_node)
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
                agent = Agent(model, system_prompt=content)
                res = await agent.run(str(kwargs.get("input_data", "")))
                return res.output

        workflow_runner.__name__ = tool.name
        workflow_runner.__doc__ = tool.description
        return workflow_runner

    def get_tool_dict(self) -> Dict[str, Callable]:
        return self._callables

class WorkflowLoader:
    def __init__(self, project_root: Path, workspace_root: Optional[Path] = None):
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.registry = WorkflowRegistry(project_root, workspace_root)

    def load_all(self, mcp_tool_names: List[str] = None, config: Optional[Dict[str, Any]] = None):
        if mcp_tool_names:
            self.registry.set_mcp_tools(mcp_tool_names)

        core_path = self.project_root / "workflows"
        workspace_path = None
        if self.workspace_root:
            workspace_path = self.workspace_root / ".brwn" / "workflows"

        for pattern in ["**/*.yaml", "**/*.yml", "**/*.md"]:
            self._scan_dir(core_path, "core", pattern, config=config)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", pattern, config=config)

        return self.registry.get_tool_dict()

    def _scan_dir(self, directory: Path, scope: str, pattern: str, config: Optional[Dict[str, Any]] = None):
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

                if file_type == "yaml":
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        description = data.get("description")
                        triggers = data.get("triggers")

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
                logger.error(f"Failed to load tool from {file_path}: {e}")
