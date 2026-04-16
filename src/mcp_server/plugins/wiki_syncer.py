from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import subprocess
import os
from typing import List

# Logger settings
logger = setup_logging(__name__)
mcp = create_mcp_server("wiki_syncer")

class WikiSyncHelper:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def run_git(self, args: List[str]) -> str:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Git execution error: {e.stderr}")
            raise Exception(f"Git Error: {e.stderr}")

@mcp.tool()
@mcp_tool_errorhandler
async def setup_wiki_remote(repo_path: str, repo_url: str) -> str:
    """Wiki用のリモート(wiki)を git remote に追加します。
    
    Args:
        repo_path: ローカルリポジトリのパス
        repo_url: メインリポジトリのURL
    """
    helper = WikiSyncHelper(repo_path)
    wiki_url = repo_url.replace(".git", ".wiki.git")
    
    remotes = helper.run_git(["remote"])
    if "wiki" not in remotes.split():
        helper.run_git(["remote", "add", "wiki", wiki_url])
        return f"Added wiki remote: {wiki_url}"
    return "Wiki remote already exists."

@mcp.tool()
@mcp_tool_errorhandler
async def sync_docs_to_wiki(repo_path: str, prefix: str = "docs", branch: str = "master") -> str:
    """指定されたディレクトリをWikiリポジトリに同期（subtree push）します。
    
    Args:
        repo_path: ローカルリポジトリのパス
        prefix: 同期対象のディレクトリ（デフォルト: docs）
        branch: 同期先のブランチ（デフォルト: master）
    """
    helper = WikiSyncHelper(repo_path)
    docs_full_path = os.path.join(repo_path, prefix)
    
    if not os.path.exists(docs_full_path):
        return f"Error: Directory '{prefix}' does not exist in {repo_path}"

    try:
        helper.run_git(["subtree", "push", f"--prefix={prefix}", "wiki", branch])
        return f"Successfully synced '{prefix}' to Wiki."
    except Exception as e:
        return f"Sync failed: {e}. (Make sure changes are committed before syncing)"

if __name__ == "__main__":
    mcp.run(transport="stdio")
