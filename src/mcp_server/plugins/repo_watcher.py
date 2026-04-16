from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import os
import time
import asyncio
import pathspec
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from typing import List

# Logger settings
logger = setup_logging(__name__)
mcp = create_mcp_server("repo_watcher")

class RepoWatcherHandler(FileSystemEventHandler):
    """リポジトリの変更を監視し、自動的な再解析トリガーを発火させるハンドラ"""
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.last_trigger = 0
        self.debounce_seconds = 2
        self.spec = self._load_gitignore()

    def _load_gitignore(self):
        gitignore_path = os.path.join(self.repo_path, ".gitignore")
        patterns = [".git/", "__pycache__/", "node_modules/"]
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                patterns.extend(f.readlines())
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def on_modified(self, event):
        if event.is_directory:
            return
        rel_path = os.path.relpath(event.src_path, self.repo_path)
        if self.spec.match_file(rel_path):
            return
        self._trigger_notification(rel_path)

    def on_created(self, event):
        self.on_modified(event)

    def _trigger_notification(self, file_path: str):
        now = time.time()
        if now - self.last_trigger < self.debounce_seconds:
            return
        self.last_trigger = now
        logger.info(f"Change detected in {file_path}. MCP notification could be triggered here.")

@mcp.tool()
@mcp_tool_errorhandler
async def watch_repositories(paths: List[str]) -> str:
    """指定されたディレクトリ群のファイル変更監視を開始します。
    
    Args:
        paths: 監視対象のディレクトリパスのリスト
    """
    observer = Observer()
    started_paths = []
    for path in paths:
        abs_path = os.path.realpath(path)
        if os.path.exists(abs_path):
            handler = RepoWatcherHandler(abs_path)
            observer.schedule(handler, abs_path, recursive=True)
            started_paths.append(abs_path)
    
    if not started_paths:
        return "No valid paths provided for watching."
    
    # Observerを別スレッドで開始
    observer.start()
    return f"Started watching: {', '.join(started_paths)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
