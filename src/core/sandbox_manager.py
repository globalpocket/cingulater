import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport
from loguru import logger
from testcontainers.core.container import DockerContainer

from src.utils.cmd_helper import run_command


class WorkspaceContext:
    def __init__(self, root_path: str, reference_path: Optional[str] = None):
        """
        ワークスペースのコンテキストを管理する。
        """
        self.root_path = Path(os.path.realpath(root_path))
        self.reference_path = (
            Path(os.path.realpath(reference_path)) if reference_path else None
        )

        logger.info(
            f"WorkspaceContext initialized. root={self.root_path}, "
            f"reference={self.reference_path}"
        )

    def resolve_path(self, target_path: str, strict: bool = True) -> str:
        """
        AIエージェントから渡されたパスを、公式 MCP も理解できる絶対パスまたは
        コンテキストルートからの相対パスとして解決する。
        セキュリティは Filesystem MCP の起動時引数 (--allowed-directories)
        で担保されるため、ここでは単純な結合と存在チェックを行う。
        """
        p = Path(target_path)
        if p.is_absolute():
            return str(p.resolve())
        return str((self.root_path / p).resolve())


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
            "test_command": "pytest",
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
                findings.append(
                    {
                        "path": item["path"],
                        "line": item["start"]["line"],
                        "message": item["extra"]["message"],
                        "severity": item["extra"]["severity"],
                    }
                )
            return {"findings": findings}
        except Exception as e:
            return {"error": f"Exception during semgrep scan: {e}"}

    async def run_lint(self, path: str = ".") -> str:
        cmd = self.get_lint_command(path)
        if not cmd:
            return "No linter found."
        # shlex.split は utils.run_command 側で処理されるか、
        # あるいは最初からリストを渡すことで shell=True を排除可能。
        result = run_command(cmd, cwd=self.repo_root)
        return result.combined or "No issues found."

    def _build_command(
        self, base_cmd: Optional[str], default_cmd: str, path: str
    ) -> List[str]:
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        rel_path = os.path.relpath(target_path, self.repo_root)
        
        full_cmd = base_cmd if base_cmd else default_cmd
        import shlex
        parts = shlex.split(full_cmd)
        parts.append(rel_path)
        return parts

    def get_lint_command(self, path: str = ".") -> List[str]:
        return self._build_command(self.config.get("lint_command"), "ruff check", path)

    def get_format_command(self, path: str = ".") -> List[str]:
        return self._build_command(self.config.get("format_command"), "black", path)

    def get_security_command(self, path: str = ".") -> List[str]:
        return self._build_command(
            self.config.get("security_command"), "bandit -r -f txt", path
        )


class SandboxManager:
    """公式 Filesystem MCP を内部で活用するサンドボックス管理クラス"""

    def __init__(self, user_id: int, group_id: int):
        self.user_id = user_id
        self.group_id = group_id
        self.context: Optional[WorkspaceContext] = None
        self._exit_stack = AsyncExitStack()
        self._fs_client: Optional[Client] = None
        logger.info(f"SandboxManager initialized for user {user_id}:{group_id}")

    async def _get_fs_client(self) -> Client:
        """Filesystem MCP クライアントを遅延起動・取得する"""
        if self._fs_client:
            return self._fs_client

        if not self.context:
            raise RuntimeError("WorkspaceContext is not set.")

        # 許可ディレクトリの設定（ワークスペースとリファレンス）
        allowed_dirs = [str(self.context.root_path)]
        if self.context.reference_path:
            allowed_dirs.append(str(self.context.reference_path))

        logger.info(
            f"Starting official Filesystem MCP server with allowed dirs: {allowed_dirs}"
        )

        transport = StdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"] + allowed_dirs,
        )

        client = Client(transport)
        await self._exit_stack.enter_async_context(client)
        self._fs_client = client
        return client

    async def stop(self):
        """内部 MCP サーバーを停止する"""
        await self._exit_stack.aclose()
        self._fs_client = None

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
        full_path = self.context.resolve_path(path)
        # 書き込み制限の基本チェック（詳細は MCP 側でもバリデーションされる）
        if rw and not str(full_path).startswith(str(self.context.root_path)):
            raise PermissionError("Write access denied outside workspace.")
        return str(full_path)

    async def list_files(self, path: str = ".", max_depth: int = 1) -> str:
        client = await self._get_fs_client()
        full_path = self._get_full_path(path)

        # 公式 MCP の list_directory を使用
        # 再帰的な探索が必要な場合はエミュレートする
        async def _recursive_list(current_path: str, depth: int) -> List[str]:
            if depth > max_depth:
                return []

            try:
                # ツール名の取得（公式 MCP の定義に従う）
                res = await client.call_tool("list_directory", {"path": current_path})
                # 結果のパース
                content = res if isinstance(res, str) else str(res)
                lines = content.strip().split("\n")

                results = []
                for line in lines:
                    results.append(line)
                    if "[DIR]" in line and depth < max_depth:
                        dir_name = line.split("  ")[-1].strip("/")
                        dir_path = os.path.join(current_path, dir_name)
                        results.extend(await _recursive_list(dir_path, depth + 1))
                return results
            except Exception as e:
                logger.warning(f"Failed to list directory {current_path}: {e}")
                return [f"Error: {e}"]

        all_files = await _recursive_list(full_path, 1)
        return "\n".join(all_files) or "(Empty directory)"

    async def read_file(self, path: str) -> str:
        client = await self._get_fs_client()
        full_path = self._get_full_path(path)
        res = await client.call_tool("read_file", {"path": full_path})
        content = res if isinstance(res, str) else str(res)
        return f"--- Contents of {path} ---\n{content}\n--- End ---"

    async def write_file(self, path: str, content: str) -> str:
        client = await self._get_fs_client()
        full_path = self._get_full_path(path, rw=True)
        # 公式 MCP の write_file は親ディレクトリが存在しないと
        # 失敗する可能性があるため、ここでは write_file ツールを呼び出す。
        # 備考: 公式 server-filesystem の write_file は
        # ディレクトリ作成機能を含む場合がある。
        await client.call_tool("write_file", {"path": full_path, "content": content})
        return f"Successfully written to {path}."

    async def run_command(
        self,
        command: Union[str, List[str]],
        image: str = "python:3.11-slim",
        environment: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Testcontainers を用いたサンドボックス内コマンド実行"""
        if not self.context or not self.context.root_path:
            raise RuntimeError("Workspace root is not set.")

        logger.info(f"Starting Sandbox Container (Testcontainers) with image: {image}")

        # Testcontainers による宣言的なコンテナ構築
        container = (
            DockerContainer(image)
            .with_bind_mount(str(self.context.root_path), "/workspace", mode="rw")
            .with_env("HOME", "/tmp")  # nosec: B108 (Container-internal path)
        )

        if environment:
            for k, v in environment.items():
                container.with_env(k, v)

        # セキュリティ制約の適用
        # 1. ネットワーク遮断
        # 2. 非Rootユーザー実行
        # 3. ワーキングディレクトリ設定
        container._container_proxy.params.update(
            {
                "network_disabled": True,
                "user": f"{self.user_id}:{self.group_id}",
                "working_dir": "/workspace",
            }
        )

        try:
            with container as c:
                cmd_to_run = (
                    ["/bin/sh", "-c", command]
                    if isinstance(command, str)
                    else command
                )
                logger.info(f"Executing inside sandbox: {cmd_to_run}")
                # コマンド実行
                result = c.get_wrapped_container().exec_run(
                    cmd=cmd_to_run,
                    user=f"{self.user_id}:{self.group_id}",
                    workdir="/workspace",
                )

                output = result.output.decode("utf-8")
                return {
                    "exit_code": result.exit_code,
                    "stdout": output,
                    "stderr": "",  # exec_run combines stdout/stderr by default
                }
        except Exception as e:
            logger.error(f"Sandbox execution failed: {e}")
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def lint_code(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_lint_command(path)
        if not cmd:
            return "No linter found."
        res = await self.run_command(cmd)
        return f"Lint Results:\nStatus: {res['exit_code']}\nOutput: {res['stdout']}"

    async def format_code(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_format_command(path)
        if not cmd:
            return "No formatter found."
        res = await self.run_command(cmd)
        return f"Format Results:\nStatus: {res['exit_code']}\nOutput: {res['stdout']}"

    async def scan_security(self, path: str = ".") -> str:
        linter = LinterEngine(str(self.context.root_path))
        cmd = linter.get_security_command(path)
        if not cmd:
            return "No security scanner found."
        res = await self.run_command(cmd)
        return (
            f"Security Scan Results:\nStatus: {res['exit_code']}\n"
            f"Output: {res['stdout']}"
        )

    def cleanup_orphans(self):
        """Testcontainers がコンテキストマネージャ経由でクリーンアップするため、
        明示的なオーファン消去は補助的な役割となる。
        """
        logger.info("Testcontainers will handle container lifecycle automatically.")
