import asyncio
import sys
import json
import os
from typing import Any, Dict

# プロジェクトルートをパスに追加
sys.path.append(os.getcwd())

from src.mcp_server.manager import MCPServerManager

async def run_mcp_tool(server_name: str, tool_name: str, arguments: Dict[str, Any]):
    """
    指定されたサーバーを起動し、ツールを実行して結果を返す。
    """
    project_root = os.getcwd()
    config_path = "config/config.yaml"
    
    # ユーザー設定
    user_id = 501
    group_id = 20
    repo_path = project_root
    reference_path = project_root
    
    async with MCPServerManager(project_root, config_path) as manager:
        if server_name == "workspace":
            client = await manager.start_workspace_server(repo_path, reference_path, user_id, group_id)
        elif server_name == "knowledge":
            client = await manager.start_knowledge_server(repo_path, ".local/share/brownie/memory", "brownie")
        else:
            # プラグインサーバーの起動
            await manager.provision_servers([server_name])
            client = manager.plugin_clients.get(server_name)
            
        if not client:
            return {"error": f"Failed to start/connect to server: {server_name}"}
            
        # ツールの実行
        # client.call_tool は fastmcp.Client のメソッド（非同期）
        try:
            # セッションが確立されるのを待つ必要がある場合があるため少し待機
            for _ in range(5):
                if client.session:
                    break
                await asyncio.sleep(0.5)
                
            result = await client.call_tool(tool_name, arguments)
            # CallToolResult は直接 JSON シリアライズできないため、文字列化または辞書化
            if hasattr(result, "content") and isinstance(result.content, list):
                # テキストコンテンツを抽出
                text_content = "\n".join([c.text for c in result.content if hasattr(c, "text")])
                return {"result": text_content}
            return {"result": str(result)}
        except Exception as e:
            return {"error": str(e)}

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python brownie_tool_bridge.py <server_name> <tool_name> <json_args>")
        sys.exit(1)
        
    server = sys.argv[1]
    tool = sys.argv[2]
    try:
        args = json.loads(sys.argv[3])
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON arguments: {sys.argv[3]}")
        sys.exit(1)
        
    res = asyncio.run(run_mcp_tool(server, tool, args))
    print(json.dumps(res, ensure_ascii=False, indent=2))
