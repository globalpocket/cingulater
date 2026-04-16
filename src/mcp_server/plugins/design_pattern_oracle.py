import ast
import os

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("design_pattern_oracle")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_design_pattern(file_path: str) -> str:
    """指定されたソースファイルのAST（抽象構文木）を解析し、適用されているデザインパターンを推測します。"""
    if not os.path.exists(file_path):
        return f"Error: File not found {file_path}"
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        tree = ast.parse(content)
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        
        patterns = []
        for cls in classes:
            # Singleton Pattern (has _instance)
            has_instance = any(isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == '_instance' for t in n.targets) for n in cls.body)
            if has_instance:
                patterns.append(f"{cls.name}: Singleton Pattern candidate")
                
            # Observer Pattern (has notify, attach, update methods)
            methods = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
            if "notify" in methods and ("attach" in methods or "add_observer" in methods):
                patterns.append(f"{cls.name}: Observer (Subject) Pattern candidate")
            if "update" in methods and "notify" not in methods:
                 patterns.append(f"{cls.name}: Observer (Listener) Pattern candidate")
                 
            # Factory Pattern
            if cls.name.endswith("Factory") or "create" in methods:
                 patterns.append(f"{cls.name}: Factory Pattern candidate")
                 
        if not patterns:
             return f"No obvious design patterns detected in {file_path}. (Analysis is limited to basic patterns)"
             
        return "Detected Design Patterns:\n" + "\n".join(patterns)
    except Exception as e:
        return f"Analysis failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
