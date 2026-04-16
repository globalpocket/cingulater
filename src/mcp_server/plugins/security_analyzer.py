import os
import subprocess

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("security_analyzer")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_security(directory: str) -> str:
    """Bandit を使用して指定されたディレクトリのセキュリティ脆弱性をスキャンします。"""
    # 簡易化のため Bandit のみをラップ。実際には Semgrep なども併用可能。
    if not os.path.exists(directory):
        return f"Error: Directory not found {directory}"
        
    try:
        # Check if bandit is installed
        result = subprocess.run(["bandit", "-r", directory, "-f", "txt"], capture_output=True, text=True)
        # bandit returns 0 for no issues, 1 for issues found.
        if "command not found" in result.stderr:
             return "Bandit is not installed in this environment."
        return f"Security Analysis Report:\n{result.stdout}\n{result.stderr}"
    except FileNotFoundError:
        return "Bandit command not found. Please ensure it's installed."
    except Exception as e:
        return f"Security scan failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
