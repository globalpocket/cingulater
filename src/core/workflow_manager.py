import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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

    def __init__(self):
        self._tools: Dict[str, WorkflowTool] = {}
        self._callables: Dict[str, Callable] = {}
        self._mcp_tool_names: List[str] = []

    def set_mcp_tools(self, mcp_tool_names: List[str]):
        """MCPツールの名前一覧を登録（優先順位チェック用）"""
        self._mcp_tool_names = mcp_tool_names

    def register_tool(self, tool: WorkflowTool, content: str):
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
        self._callables[tool.name] = self._create_callable(tool, content)
        logger.info(
            f"Registered {tool.file_type} tool '{tool.name}' from {tool.scope} scope."
        )

    def _create_callable(self, tool: WorkflowTool, content: str) -> Callable:
        """将来の拡張性を考慮したスタブ Callable を生成する"""

        async def stub_wrapper(**kwargs):
            if tool.file_type == "yaml":
                # TODO: ここでLangGraphのコンパイルとinvokeを実行する
                return f"Executing workflow {tool.name} with args: {kwargs}"
            else:
                # TODO: ここでPydantic AIのエージェント呼び出しなどを実行する
                # 現状はプロンプト内容自体を返すか、スタブメッセージを返す
                return (
                    f"Prompt helper '{tool.name}' called. "
                    f"(Prompt content length: {len(content)})"
                )

        # メタデータの付与
        stub_wrapper.__name__ = tool.name
        stub_wrapper.__doc__ = (
            tool.description or f"BROWNIE {tool.file_type} tool: {tool.name}"
        )

        # 追加属性の付与 (triggers 等)
        if tool.triggers:
            setattr(stub_wrapper, "triggers", tool.triggers)

        return stub_wrapper

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
        self.registry = WorkflowRegistry()

    def load_all(self, mcp_tool_names: List[str] = None):
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
            self._scan_dir(core_path, "core", ext)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", ext)

        for ext in ["*.md"]:
            self._scan_dir(core_path, "core", ext)
            if workspace_path:
                self._scan_dir(workspace_path, "workspace", ext)

        return self.registry.get_tool_dict()

    def _scan_dir(self, directory: Path, scope: str, pattern: str):
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

                self.registry.register_tool(tool, content)

            except Exception as e:
                if isinstance(e, ValueError):
                    raise e
                logger.error(f"Failed to load tool from {file_path}: {e}")
