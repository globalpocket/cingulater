import ast
import os

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("arch_diagram")

@mcp.tool()
@mcp_tool_errorhandler
async def generate_mermaid_diagram(directory: str) -> str:
    """指定されたディレクトリ内のPythonファイルの依存関係からMermaid.jsのクラス図（概略）を生成します。"""
    if not os.path.exists(directory):
        return f"Error: Directory not found {directory}"
        
    mermaid_lines = ["classDiagram"]
    
    for root, _, files in os.walk(directory):
        for file in files:
            if not file.endswith(".py"):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content)
                classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
                imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
                
                for cls in classes:
                    mermaid_lines.append(f"  class {cls.name}")
                    # Bases
                    for base in cls.bases:
                        if isinstance(base, ast.Name):
                            mermaid_lines.append(f"  {base.id} <|-- {cls.name}")
            except Exception:
                pass # skip
                
    return "```mermaid\n" + "\n".join(mermaid_lines) + "\n```"

if __name__ == "__main__":
    mcp.run(transport="stdio")
