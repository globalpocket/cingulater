import json
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional, Union, List

from testcontainers.core.container import DockerContainer
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from src.utils.cmd_helper import run_command


class WorkspaceContext:
    def __init__(self, root_path: str, reference_path: Optional[str] = None):
        """
        ワークスペースのコンテキストを管理する。
        """
        self.root_path = Path(os.path.realpath(root_path))
        self.reference_path = Path(os.path.realpath(reference_path)) if reference_path else None
        
        logger.info(f"WorkspaceContext initialized. root={self.root_path}, reference={self.reference_path}")

    def resolve_path(self, target_path: str, strict: bool = True) -> Path:
        """
        AIエージェントから渡されたパスを安全な物理絶対パスに解決する。
        """
        p = Path(target_path)
        
        if p.is_absolute():
            full_path = p.resolve()
        else:
            full_path = (self.root_path / p).resolve()

        if strict:
            if not self._is_within(full_path, self.root_path):
                if self.reference_path and self._is_within(full_path, self.reference_path):
                    return full_path
                
                logger.error(f"Security Alert: Path Traversal attempt detected: {target_path} -> {full_path}")
                raise PermissionError(f"Access denied. Path '{target_path}' is outside the authorized workspace.")
        
        return full_path

    def get_relative_path(self, absolute_path: Union[str, Path]) -> str:
        abs_p = Path(absolute_path).resolve()
        try:
            return os.path.relpath(abs_p, self.root_path)
        except ValueError:
            if self.reference_path:
                try:
                    return os.path.relpath(abs_p, self.reference_path)
                except ValueError:
                    pass
            return str(abs_p)

    def _is_within(self, child: Path, parent: Path) -> bool:
        try:
            return child.resolve().is_relative_to(parent.resolve())
        except (ValueError, AttributeError):
            try:
                os.relpath(child.resolve(), parent.resolve())
                return not os.path.relpath(child.resolve(), parent.resolve()).startswith("..")
            except ValueError:
                return False

class LinterEngine:
    """各種リンター・フォーマッター・セキュリティスキャナの一括実行エンジン"""

    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.config = self._load_repo_config()

    def _load_repo_config(self) -> Dict[str, Any]:
        config_path = os.path.join(self.repo_root, ".brwn.json")
        default_config = {
            "lint_command": None,
            "format_command": None,
            "security_command": None,
            "test_command": "pytest"
        }
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    repo_config = json.load(f)
                    return {**default_config, **repo_config}
            except Exception as e:
                logger.warning(f"Failed to load .brwn.json: {e}")
        return default_config

    async def scan_semgrep(self, path: str = ".") -> Dict[str, Any]:
        target_path = os.path.join(self.repo_root, path)
        cmd = ["semgrep", "scan", "--config=auto", "--json", "."]
        result = run_command(cmd, cwd=target_path)
        if result.exit_code not in (0, 1):
            return {"error": f"Semgrep failed: {result.stderr}"}
        try:
            data = json.loads(result.stdout)
            findings = []
            for item in data.get("results", []):
                findings.append({
                    "path": item["path"],
                    "line": item["start"]["line"],
                    "message": item["extra"]["message"],
                    "severity": item["extra"]["severity"]
                })
            return {"findings": findings}
        except Exception as e:
            return {"error": f"Exception during semgrep scan: {e}"}

    async def run_lint(self, path: str = ".") -> str:
        cmd_str = self.get_lint_command(path)
        if not cmd_str: return "No linter found."
        result = run_command(cmd_str, cwd=self.repo_root, shell=True)
        return result.combined or "No issues found."

    def get_lint_command(self, path: str = ".") -> Optional[str]:
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("lint_command")
        if not cmd:
            if any(f.endswith(".py") for _, _, fs in os.walk(target_path) for f in fs):
                cmd = "ruff check"
        if not cmd: return None
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

    def get_format_command(self, path: str = ".") -> Optional[str]:
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("format_command")
        if not cmd:
            if any(f.endswith(".py") for _, _, fs in os.walk(target_path) for f in fs):
                cmd = "black"
        if not cmd: return None
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

    def get_security_command(self, path: str = ".") -> Optional[str]:
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("security_command")
        if not cmd:
            if any(f.endswith(".py") for _, _, fs in os.walk(target_path) for f in fs):
                cmd = "bandit -r -f txt"
        if not cmd: return None
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

class SandboxManager:
    """Testcontainers を用いたサンドボックス管理クラス"""
    
    def __init__(self, user_id: int, group_id: int):
        self.user_id = user_id
        self.group_id = group_id
        self.context: Optional[WorkspaceContext] = None
        logger.info(f"SandboxManager initialized for user {user_id}:{group_id}")

    def set_workspace_root(self, root_path: str):
        if self.context:
            self.context.root_path = Path(os.path.realpath(root_path))
        else:
            self.context = WorkspaceContext(root_path)

    def set_reference_root(self, ref_path: str):
        if not self.context:
            self.context = WorkspaceContext(".", ref_path)
        else:
            self.context.reference_path = Path(os.path.realpath(ref_path))

    def _get_full_path(self, path: str, rw: bool = False) -> str:
        if not self.context:
            raise RuntimeError("WorkspaceContext is not set.")
        full_path = self.context.resolve_path(path, strict=True)
        if rw and not str(full_path).startswith(str(self.context.root_path)):
            raise PermissionError("Write access denied outside workspace.")
        return str(full_path)

    async def list_files(self, path: str = ".", max_depth: int = 1) -> str:
        full_path = self._get_full_path(path)
        if not os.path.exists(full_path):
            return f"Error: Path '{path}' not found."
        output = []
        for root, dirs, files in os.walk(full_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_root = os.path.relpath(root, full_path)
            prefix = "" if rel_root == "." else rel_root + "/"
            for d in sorted(dirs): output.append(f"[DIR]  {prefix}{d}/")
            for f in sorted(files):
                if not f.startswith("."): output.append(f"[FILE] {prefix}{f}")
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if depth >= max_depth: del dirs[:]
        return "\n".join(output) or "(Empty directory)"

    async def read_file(self, path: str) -> str:
        full_path = self._get_full_path(path)
        if not os.path.exists(full_path): return f"Error: File '{path}' not found."
        with open(full_path, "r", encoding="utf-8") as f:
            return f"--- Contents of {path} ---\n{f.read()}\n--- End ---"

    async def write_file(self, path: str, content: str) -> str:
        full_path = self._get_full_path(path, rw=True)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully written to {path}."

    async def run_command(
        self, 
        command: str, 
        image: str = "python:3.11-slim", 
        environment: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Testcontainers を用いたサンドボックス内コマンド実行"""
        if not self.context or not self.context.root_path:
            raise RuntimeError("Workspace root is not set.")

        logger.info(f"Starting Sandbox Container (Testcontainers) with image: {image}")
        
        # Testcontainers による宣言的なコンテナ構築
        container = (
            DockerContainer(image)
            .with_bind_mount(str(self.context.root_path), "/workspace", mode="rw")
            .with_env("HOME", "/tmp")
        )
        
        if environment:
            for k, v in environment.items():
                container.with_env(k, v)

        # セキュリティ制約の適用
        # 1. ネットワーク遮断
        # 2. 非Rootユーザー実行
        # 3. ワーキングディレクトリ設定
        container._container_proxy.params.update({
            "network_disabled": True,
            "user": f"{self.user_id}:{self.group_id}",
            "working_dir": "/workspace"
        })

        try:
            with container as c:
                logger.info(f"Executing inside sandbox: {command}")
                # コマンド実行
                result = c.get_wrapped_container().exec_run(
                    cmd=["/bin/sh", "-c", command],
                    user=f"{self.user_id}:{self.group_id}",
                    workdir="/workspace"
                )
                
                output = result.output.decode("utf-8")
                return {
                    "exit_code": result.exit_code,
                    "stdout": output,
                    "stderr": "" # exec_run combines stdout/stderr by default
                }
        except Exception as e:
            logger.error(f"Sandbox execution failed: {e}")
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def lint_code(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_lint_command(path)
        if not cmd: return "No linter found."
        res = await self.run_command(cmd)
        return f"Lint Results:\nStatus: {res['exit_code']}\nOutput: {res['stdout']}"

    async def format_code(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_format_command(path)
        if not cmd: return "No formatter found."
        res = await self.run_command(cmd)
        return f"Format Results:\nStatus: {res['exit_code']}\nOutput: {res['stdout']}"

    async def scan_security(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_security_command(path)
        if not cmd: return "No security scanner found."
        res = await self.run_command(cmd)
        return f"Security Scan Results:\nStatus: {res['exit_code']}\nOutput: {res['stdout']}"

    def cleanup_orphans(self):
        """Testcontainers がコンテキストマネージャ経由でクリーンアップするため、
        明示的なオーファン消去は補助的な役割となる。
        """
        logger.info("Testcontainers will handle container lifecycle automatically.")
