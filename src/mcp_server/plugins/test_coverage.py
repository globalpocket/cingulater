import os
import subprocess

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("test_coverage")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_coverage(directory: str) -> str:
    """pytest-covを使用してテストカバレッジを測定し、未テストの領域を特定します。"""
    if not os.path.exists(directory):
        return f"Error: Directory not found {directory}"
        
    try:
        # pytest -q --cov=src tests/  を実行する想定
        # 環境によっては動作しないため、適宜コマンド調整が必要
        result = subprocess.run(
            ["uvx", "pytest", "--cov", directory], 
            capture_output=True, 
            text=True
        )
        return f"Test Coverage Report:\n{result.stdout}\n{result.stderr}"
    except Exception as e:
        return f"Coverage analysis failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
