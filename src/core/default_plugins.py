import os
import sys
from typing import Any, Dict, List, Optional

from .plugin_specs import hookimpl


class DirectoryDiscoveryPlugin:
    """src/mcp_server/plugins/ ディレクトリ下のファイルを MCP プラグインとして自動登録するデフォルト実装"""
    
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.plugins_dir = os.path.join(project_root, "src", "mcp_server", "plugins")

    @hookimpl
    def get_plugin_names(self) -> List[str]:
        if not os.path.exists(self.plugins_dir):
            return []
        names = []
        for f in os.listdir(self.plugins_dir):
            if f.endswith(".py") and not f.startswith("__"):
                names.append(f[:-3])
        return names

    @hookimpl
    def get_server_config(self, name: str) -> Optional[Dict[str, Any]]:
        # get_plugin_names() に含まれるかチェック（セキュリティ・境界チェック）
        if name not in self.get_plugin_names():
            return None
            
        return {
            "name": name,
            "command": sys.executable,
            "args": ["-m", f"src.mcp_server.plugins.{name}"],
            "env": {"PYTHONPATH": "."}
        }
