from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import subprocess
import os

logger = setup_logging(__name__)
mcp = create_mcp_server("git_archeology")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_git_history(file_path: str, line_start: int = None, line_end: int = None) -> str:
    """指定されたファイル（または特定の行）の過去のコミット履歴（git blame や log）を解析します。"""
    if not os.path.exists(file_path):
        return f"Error: File not found {file_path}"
        
    try:
        if line_start and line_end:
            # git blame -L <start>,<end>
            cmd = ["git", "blame", "-L", f"{line_start},{line_end}", file_path]
        else:
            # 簡易ログ
            cmd = ["git", "log", "--oneline", "-n", "10", "--", file_path]
            
        result = subprocess.run(cmd, capture_output=True, text=True)
        return f"Git History Analysis:\n{result.stdout}\n{result.stderr}"
    except Exception as e:
        return f"Archeology failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
