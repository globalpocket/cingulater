import os

import git

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("git_archeology")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_git_history(
    file_path: str, 
    line_start: int = None, 
    line_end: int = None, 
    repo_path: str = "."
) -> str:
    """指定されたファイルの過去のコミット履歴を解析します。"""
    if not os.path.exists(file_path):
        return f"Error: File not found {file_path}"
        
    try:
        repo = git.Repo(repo_path, search_parent_directories=True)
        if line_start and line_end:
            # git blame -L <start>,<end>
            blame_output = repo.git.blame("-L", f"{line_start},{line_end}", file_path)
            return f"Git History Analysis (Blame):\n{blame_output}"
        else:
            # 簡易ログ
            log_output = repo.git.log("--oneline", "-n", "10", "--", file_path)
            return f"Git History Analysis (Log):\n{log_output}"
    except Exception as e:
        return f"Archeology failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
