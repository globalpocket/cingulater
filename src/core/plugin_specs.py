from typing import Any, Dict, List, Optional

import pluggy

hookspec = pluggy.HookspecMarker("brownie")
hookimpl = pluggy.HookimplMarker("brownie")

class MCPPluginSpec:
    """BROWNIE MCP プラグインの Hook Specification"""
    
    @hookspec
    def get_server_config(self, name: str) -> Optional[Dict[str, Any]]:
        """
        指定された名前のプラグインサーバーの起動設定を返します。
        
        Returns:
            Dict[str, Any]: {
                "command": str,
                "args": List[str],
                "env": Optional[Dict[str, str]]
            } または None
        """
        pass

    @hookspec
    def get_plugin_names(self) -> List[str]:
        """このプラグインセットが提供するプラグイン名のリストを返します。"""
        pass
