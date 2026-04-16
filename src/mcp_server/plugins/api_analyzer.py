import ast
import os

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("api_analyzer")

@mcp.tool()
@mcp_tool_errorhandler
async def extract_api_endpoints(file_path: str) -> str:
    """FastAPIやFlaskなどのルーティングデコレータを解析してAPIエンドポイント仕様を抽出します。"""
    if not os.path.exists(file_path):
        return f"Error: File not found {file_path}"
        
    try:
        endpoints = []
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and getattr(deco.func, 'attr', '') in ['get', 'post', 'put', 'delete', 'route']:
                        method = deco.func.attr.upper()
                        path_arg = ""
                        if deco.args and isinstance(deco.args[0], ast.Constant):
                            path_arg = deco.args[0].value
                        endpoints.append(f"[{method}] {path_arg} -> {node.name}")
                        
        if not endpoints:
             return "No API endpoints detected (searching for get/post/put/delete decorators)."
        return "Detected Endpoints:\n" + "\n".join(endpoints)
    except Exception as e:
        return f"Extraction failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
