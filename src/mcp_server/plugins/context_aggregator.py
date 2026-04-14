from fastmcp import FastMCP
import subprocess
import os
import logging
from typing import List, Optional

# Logger settings
logger = logging.getLogger(__name__)

mcp = FastMCP("context_aggregator")

@mcp.tool()
async def run_repomix_discovery(repo_path: str, exclude_patterns: Optional[List[str]] = None) -> str:
    """Repomixを使用してリポジトリのコンテキストを1つのMarkdownファイルに集約します。
    大規模なファイルや不要なディレクトリを除外して、LLMに最適なコンテキストを生成します。
    
    Args:
        repo_path: 対象リポジトリのローカルパス
        exclude_patterns: 除外したいファイルパターンのリスト（例: ["*.log", "tmp/**"]）
    """
    abs_repo_path = os.path.realpath(repo_path)
    if not os.path.exists(abs_repo_path):
        return f"Error: Repository path not found: {abs_repo_path}"

    output_file = os.path.join(abs_repo_path, ".brownie_repomix.md")
    
    if exclude_patterns is None:
        exclude_patterns = ["docs/**", "wiki/**", "*.md", ".git/**", ".brwn/**"]
    
    exclude_str = ",".join(exclude_patterns)
    
    try:
        # npx repomix の実行
        cmd = [
            "npx", "-y", "repomix",
            "--output", output_file,
            "--ignore", exclude_str,
            "--include", "**/*"
        ]
        
        logger.info(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=abs_repo_path, capture_output=True, text=True, check=True)
        
        if os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # 完了後、一時ファイルを削除する場合はここで
            # os.remove(output_file)
            return content
        else:
            return f"Repomix failed to generate output. Logs: {result.stderr}"
            
    except subprocess.CalledProcessError as e:
        logger.error(f"Repomix runner failed: {e.stderr}")
        return f"Repomix execution failed: {e.stderr}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
