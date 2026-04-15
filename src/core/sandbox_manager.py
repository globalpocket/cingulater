import docker
import os
import yaml
import logging
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

class WorkspaceContext:
    def __init__(self, root_path: str, reference_path: Optional[str] = None):
        """
        ワークスペースのコンテキストを管理する。
        
        Args:
            root_path: ワークスペースのルート（書き込み可能、優先読み込み）
            reference_path: 参照用ルート（読み取り専用、フォールバック用）
        """
        self.root_path = Path(os.path.realpath(root_path))
        self.reference_path = Path(os.path.realpath(reference_path)) if reference_path else None
        
        logger.info(f"WorkspaceContext initialized. root={self.root_path}, reference={self.reference_path}")

    def resolve_path(self, target_path: str, strict: bool = True) -> Path:
        """
        AIエージェントから渡されたパスを安全な物理絶対パスに解決する。
        
        Args:
            target_path: 解決したいパス（相対・絶対いずれも可）
            strict: Trueの場合、root_path 外へのアクセスを禁止する (Path Traversal 防御)
            
        Returns:
            Path: 解決された絶対パス
            
        Raises:
            PermissionError: 境界外へのアクセスが検出された場合
        """
        # 1. パスの正規化
        p = Path(target_path)
        
        if p.is_absolute():
            full_path = p.resolve()
        else:
            full_path = (self.root_path / p).resolve()

        # 2. 境界チェック
        if strict:
            if not self._is_within(full_path, self.root_path):
                # 読み取り操作の場合、reference_path 内にあれば許可（フォールバック）
                if self.reference_path and self._is_within(full_path, self.reference_path):
                    return full_path
                
                logger.error(f"Security Alert: Path Traversal attempt detected: {target_path} -> {full_path}")
                raise PermissionError(f"Access denied. Path '{target_path}' is outside the authorized workspace.")
        
        return full_path

    def get_relative_path(self, absolute_path: Union[str, Path]) -> str:
        """
        絶対パスをリポジトリルートからの相対パスに変換する。
        AIへの出力時に使用。
        """
        abs_p = Path(absolute_path).resolve()
        try:
            return os.path.relpath(abs_p, self.root_path)
        except ValueError:
            # root_path 外の場合
            if self.reference_path:
                try:
                    return os.path.relpath(abs_p, self.reference_path)
                except ValueError:
                    pass
            return str(abs_p)

    def _is_within(self, child: Path, parent: Path) -> bool:
        """child が parent の配下にあるか判定する"""
        try:
            # Python 3.9+ supports is_relative_to
            return child.resolve().is_relative_to(parent.resolve())
        except (ValueError, AttributeError):
            # Fallback for even older versions or unexpected errors
            try:
                os.path.relpath(child.resolve(), parent.resolve())
                return not os.path.relpath(child.resolve(), parent.resolve()).startswith("..")
            except ValueError:
                return False

class LinterEngine:
    """各種リンター・フォーマッター・セキュリティスキャナの一括実行エンジン"""

    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.config = self._load_repo_config()

    def _load_repo_config(self) -> Dict[str, Any]:
        """リポジトリ固有の設定 (.brwn.json) を読み込む"""
        config_path = os.path.join(self.repo_root, ".brwn.json")
        default_config = {
            "lint_command": None, # None の場合はデフォルトを使用
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
        """Semgrep によるセマンティック・スキャンを実行"""
        # サンドボックス外（ホスト側）での実行を想定しつつ、パスを調整
        target_path = os.path.join(self.repo_root, path)
        try:
            # semgrep scan --config=auto --json
            result = subprocess.run(
                ["semgrep", "scan", "--config=auto", "--json", "."],
                cwd=target_path,
                capture_output=True,
                text=True
            )
            if result.returncode not in (0, 1): # 1 は指摘ありの場合
                return {"error": f"Semgrep failed: {result.stderr}"}
            
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

    async def scan_astgrep(self, query: str = None, path: str = ".") -> Dict[str, Any]:
        """ast-grep (sg) による構造的スキャンを実行"""
        target_path = os.path.join(self.repo_root, path)
        try:
            # デフォルトの sg scan --report-style json を実行
            cmd = ["sg", "scan", "--report-style", "json"]
            if query:
                # パターン指定がある場合は sg run -p ... --json
                cmd = ["sg", "run", "-p", query, "--json"]

            result = subprocess.run(
                cmd,
                cwd=target_path,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                # 0 以外でも findings がある場合があるので、stdout が JSON か確認
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"error": f"ast-grep failed: {result.stderr or result.stdout}"}
            else:
                data = json.loads(result.stdout)
            
            findings = []
            # sg scan の出力形式 (list of findings) を想定
            # query指定時(sg run)はフラットなリスト、scan時はルールごとの場合があるので適宜調整
            items = data if isinstance(data, list) else data.get("results", [])
            for item in items:
                findings.append({
                    "path": item.get("file", "unknown"),
                    "line": item.get("range", {}).get("start", {}).get("line", 0) + 1,
                    "message": item.get("message", "Pattern matched"),
                    "severity": "info" # sg はルールにより異なるが、一旦 info
                })
            return {"findings": findings}
        except Exception as e:
            return {"error": f"Exception during ast-grep scan: {e}"}

    def _get_tool_cmd(self, tool_name: str) -> str:
        """仮想環境 (.venv) 内のツールパスを優先して取得する"""
        venv_bin = os.path.join(self.repo_root, ".venv", "bin", tool_name)
        if os.path.exists(venv_bin):
            return venv_bin
        return tool_name

    def _detect_py(self, target_path: str) -> bool:
        """パスが Python ファイル、または Python ファイルを含むディレクトリか判定"""
        if os.path.isfile(target_path):
            return target_path.endswith(".py")
        if os.path.isdir(target_path):
            return any(f.endswith(".py") for _, _, fs in os.walk(target_path) for f in fs)
        return False

    def _detect_js(self, target_path: str) -> bool:
        """パスが JS/TS ファイル、またはそれらを含むディレクトリか判定"""
        exts = (".js", ".ts", ".jsx", ".tsx")
        if os.path.isfile(target_path):
            return target_path.endswith(exts)
        if os.path.isdir(target_path):
            return any(f.endswith(exts) for _, _, fs in os.walk(target_path) for f in fs)
        return False

    def get_lint_command(self, path: str = ".") -> Optional[str]:
        """適用可能なリンターコマンドを特定して返す"""
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("lint_command")
        
        if not cmd:
            if self._detect_py(target_path):
                cmd = f"{self._get_tool_cmd('ruff')} check"
            elif self._detect_js(target_path):
                cmd = "npx eslint"
        
        if not cmd:
            return None
            
        # ターゲットのパスを追加して返す
        # リポジトリ内での相対パスを使用
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

    async def run_lint(self, path: str = ".") -> str:
        """リンター (Ruff / ESLint) の実行 (ホスト側)"""
        cmd_str = self.get_lint_command(path)
        if not cmd_str:
            return "No linter found for this path."

        try:
            result = subprocess.run(cmd_str.split(), cwd=self.repo_root, capture_output=True, text=True)
            return (result.stdout + "\n" + result.stderr).strip() or "No issues found."
        except Exception as e:
            return f"Linter error: {e}"

    def get_format_command(self, path: str = ".") -> Optional[str]:
        """適用可能なフォーマットコマンドを特定して返す"""
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("format_command")
        
        if not cmd:
            if self._detect_py(target_path):
                cmd = f"{self._get_tool_cmd('black')}"
            elif self._detect_js(target_path):
                cmd = "npx prettier --write"
        
        if not cmd:
            return None
        
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

    async def run_format(self, path: str = ".") -> str:
        """フォーマッター (Black / Prettier) の実行 (ホスト側)"""
        cmd_str = self.get_format_command(path)
        if not cmd_str:
            return "No formatter found for this path."

        try:
            result = subprocess.run(cmd_str.split(), cwd=self.repo_root, capture_output=True, text=True)
            return f"Formatter output: {result.stdout.strip() or 'Success'}"
        except Exception as e:
            return f"Formatter error: {e}"

    def get_security_command(self, path: str = ".") -> Optional[str]:
        """適用可能なセキュリティスキャンコマンドを特定して返す"""
        target_path = os.path.normpath(os.path.join(self.repo_root, path))
        cmd = self.config.get("security_command")
        
        if not cmd:
            if self._detect_py(target_path):
                # Docker内などで実行しやすいよう相対パスを想定
                cmd = f"{self._get_tool_cmd('bandit')} -r -f txt"
        
        if not cmd:
            return None
        
        rel_path = os.path.relpath(target_path, self.repo_root)
        return f"{cmd} {rel_path}"

    async def scan_security(self, path: str = ".") -> str:
        """セキュリティスキャン (Bandit 等) の実行 (ホスト側)"""
        cmd_str = self.get_security_command(path)
        if not cmd_str:
            return "No security scanner found for this path."

        try:
            result = subprocess.run(cmd_str.split(), cwd=self.repo_root, capture_output=True, text=True)
            if result.returncode == 0:
                return "No security issues found."
            return f"Security Issues found:\n{result.stdout.strip()}"
        except Exception as e:
            return f"Security scan error: {e}"

class SandboxManager:
    def __init__(self, user_id: int, group_id: int):
        self.user_id = user_id
        self.group_id = group_id
        self.context = None
        try:
            self.client = docker.from_env()
            self.client.ping()
        except Exception:
            # Mac / Linux の標準的なソケットパスを試行 (設計書 11.2 補足)
            paths = [
                f"unix://{os.path.expanduser('~/.docker/run/docker.sock')}",
                "unix:///var/run/docker.sock"
            ]
            self.client = None
            for path in paths:
                try:
                    self.client = docker.DockerClient(base_url=path)
                    self.client.ping()
                    break
                except Exception:
                    self.client = None
            
            if not self.client:
                # 最終的なフォールバック（エラーメッセージを分かりやすくする）
                raise RuntimeError("Docker daemon not found. Please ensure Docker Desktop is running. "
                                 "On Mac, you may need to set: export DOCKER_HOST='unix://$HOME/.docker/run/docker.sock'")

    def sanitize_compose_yaml(self, yaml_content: str) -> str:
        """YAMLサニタイザー (設計書 8. サンドボックス & 実行環境)
        privileged, volumesマウント等の攻撃をブロックし、
        非Root実行ユーザーを指定する。
        """
        data = yaml.safe_load(yaml_content)
        
        # サービスごとのループ
        if 'services' in data:
            for service_name, config in data['services'].items():
                # 1. privileged 禁止
                if 'privileged' in config:
                    logger.warning(f"Removing privileged flag from {service_name}")
                    del config['privileged']
                
                # 2. volumes マウントの制限 (ホスト側のマウントを禁止、名前付きボリュームのみ許可など)
                # 設計上は workspace ディレクトリのみマウントするように調整
                if 'volumes' in config:
                    new_volumes = []
                    for vol in config['volumes']:
                        if isinstance(vol, str) and ":" in vol:
                            # ホストパスが "/etc" や "/" であれば削除
                            host_path = vol.split(":")[0]
                            if host_path in ["/", "/etc", "/root", "/var/run/docker.sock"]:
                                logger.warning(f"Forbidden volume mount detected: {vol}")
                                continue
                        new_volumes.append(vol)
                    config['volumes'] = new_volumes
                
                # 3. 実行ユーザーの指定 (設計書 3.2: ホスト側の権限ロック回避)
                config['user'] = f"{self.user_id}:{self.group_id}"
                
                # 4. ネットワーク隔離
                # デフォルトでは分離されたブリッジネットワークを使用
        
        return yaml.dump(data)

    def dump(self, data): # Added for compatibility if needed
        return yaml.dump(data)

    def set_workspace_root(self, root_path: str):
        """ワークスペースのルートパスを設定する"""
        if self.context:
            self.context.root_path = os.path.realpath(root_path)
        else:
            self.context = WorkspaceContext(root_path)
        logger.info(f"Sandbox workspace root set via context: {self.context.root_path}")

    def set_reference_root(self, ref_path: str):
        """参照用（ローカル）ルートを設定する"""
        if not self.context:
            # root がない状態で ref だけ設定されるケースは想定外だが一応対応
            self.context = WorkspaceContext(".", ref_path)
        else:
            self.context.reference_path = os.path.realpath(ref_path)
        logger.info(f"Sandbox reference root set via context: {self.context.reference_path}")

    def _get_full_path(self, path: str, rw: bool = False) -> str:
        """パスの解決を WorkspaceContext に委譲する"""
        if not self.context:
            raise RuntimeError("WorkspaceContext (root) is not set.")
        
        # WorkspaceContext.resolve_path は Path オブジェクトを返す
        full_path = self.context.resolve_path(path, strict=True)
        
        # 書き込み操作の場合、root_path 内であることを追加確認（Context側でも strict なら弾かれるが念押し）
        if rw:
            if not str(full_path).startswith(str(self.context.root_path)):
                logger.error(f"Write operation denied outside workspace: {full_path}")
                raise PermissionError("Write access denied outside the workspace area.")
        
        return str(full_path)

    async def list_files(self, path: str = ".", max_depth: int = 1) -> str:
        """指定されたパスのファイル一覧を取得する (max_depth で制御可能)"""
        full_path = self._get_full_path(path, rw=False)
        if not os.path.exists(full_path):
            # パスが見わからない場合、例外を投げずに AI へのヒントを返して自律復旧を促す
            return f"Error: Path '{path}' not found. Please check the directory structure (e.g., did you mean 'src/{path}'?)."
        
        output = []
        for root, dirs, files in os.walk(full_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_root = os.path.relpath(root, full_path)
            prefix = "" if rel_root == "." else rel_root + "/"
            
            # 整形
            output_items = []
            for d in sorted(dirs):
                output_items.append(f"[DIR]  {prefix}{d}/")
            for f in sorted(files):
                if not f.startswith("."):
                    output_items.append(f"[FILE] {prefix}{f}")
            
            output.extend(output_items)
            
            # 深さ制限
            current_depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if current_depth >= max_depth:
                del dirs[:]
        
        if not output:
            return "(Empty directory)"
            
        return "\n".join(output)

    async def read_file(self, path: str) -> str:
        """ファイル内容を読み取る"""
        full_path = self._get_full_path(path, rw=False)
        if not os.path.exists(full_path):
            return f"Error: File '{path}' not found. Please check if you missed a directory prefix (e.g., 'src/{path}'). Verify with list_files."
        if os.path.isdir(full_path):
            return f"Error: '{path}' is a directory. Use list_files to list its contents."
        
        # macOS などの大文字小文字を区別しないファイルシステムへの対策（厳密チェック）
        dirname, basename = os.path.split(os.path.realpath(full_path))
        if basename and basename not in os.listdir(dirname):
            return f"Error: File '{path}' not found (case-sensitive check failed). Verify the exact spelling."
        
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not content:
                return f"(File {path} is empty)"
            return f"--- Contents of {path} (Full) ---\n{content}\n--- End of {path} ---"

    async def write_file(self, path: str, content: str) -> str:
        """ファイルに内容を書き込む"""
        full_path = self._get_full_path(path, rw=True)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully written to {path}."

    async def run_command(self, command: str, image: str = "python:3.11-slim", environment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        サンドボックス（Dockerコンテナ）内でコマンドを実行する。
        """
        if not self.context or not self.context.root_path:
            raise RuntimeError("Workspace root is not set. Cannot run sandbox command.")

        logger.info(f"Running sandbox command: {command} in image: {image}")
        
        try:
            # ワークスペースをコンテナ内の /workspace にマウント
            volumes = {
                str(self.context.root_path): {"bind": "/workspace", "mode": "rw"}
            }
            
            # 非特権ユーザーで実行
            container = self.client.containers.run(
                image,
                command,
                volumes=volumes,
                working_dir="/workspace",
                user=f"{self.user_id}:{self.group_id}",
                environment=environment,
                detach=False,
                stdout=True,
                stderr=True,
                remove=True, # 実行後にコンテナを削除
                network_disabled=True # ネットワーク隔離（必要に応じて）
            )
            
            # 同期的実行の場合、戻り値は bytes 型の stdout/stderr
            output = container.decode("utf-8") if isinstance(container, bytes) else str(container)
            
            return {
                "exit_code": 0,
                "stdout": output,
                "stderr": ""
            }
        except docker.errors.ContainerError as e:
            logger.error(f"Sandbox command failed with exit code {e.exit_status}")
            return {
                "exit_code": e.exit_status,
                "stdout": e.stdout.decode("utf-8") if e.stdout else "",
                "stderr": e.stderr.decode("utf-8") if e.stderr else ""
            }
        except Exception as e:
            logger.error(f"Failed to run sandbox command: {e}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e)
            }

    async def lint_code(self, path: str = ".") -> str:
        """リンターをサンドボックス内で実行する"""
        if not self.context or not self.context.root_path:
            return "Error: Workspace root is not set."
            
        linter = LinterEngine(self.context.root_path)
        cmd = linter.get_lint_command(path)
        if not cmd:
            return "No linter found for this path."
            
        res = await self.run_command(cmd)
        return f"Lint Results (Sandbox):\nStatus: {res['exit_code']}\nOutput: {res['stdout'] or res['stderr']}"

    async def format_code(self, path: str = ".") -> str:
        """フォーマッターをサンドボックス内で実行する"""
        if not self.context or not self.context.root_path:
            return "Error: Workspace root is not set."

        linter = LinterEngine(self.context.root_path)
        cmd = linter.get_format_command(path)
        if not cmd:
            return "No formatter found for this path."

        res = await self.run_command(cmd)
        return f"Format Results (Sandbox):\nStatus: {res['exit_code']}\nOutput: {res['stdout'] or res['stderr']}"

    async def scan_security(self, path: str = ".") -> str:
        """セキュリティスキャンをサンドボックス内で実行する"""
        if not self.context or not self.context.root_path:
            return "Error: Workspace root is not set."

        linter = LinterEngine(self.context.root_path)
        cmd = linter.get_security_command(path)
        if not cmd:
            return "No security scanner found for this path."

        res = await self.run_command(cmd)
        return f"Security Scan Results (Sandbox):\nStatus: {res['exit_code']}\nOutput: {res['stdout'] or res['stderr']}"

    async def run_semgrep(self, target: str = ".") -> Dict[str, Any]:
        """Semgrep による静的解析をサンドボックス（Docker）内で実行する"""
        if not self.context or not self.context.root_path:
            return {"exit_code": -1, "logs": "Error: Workspace root is not set."}

        # セマンティック解析を実行
        # イメージは Semgrep 公式を使用
        res = await self.run_command(
            command="semgrep scan --config=auto --json .",
            image="returntocorp/semgrep",
            environment={"HOME": "/tmp"}
        )
        return {
            "status": "success" if res["exit_code"] in (0, 1) else "failed",
            "exit_code": res["exit_code"],
            "logs": res["stdout"] or res["stderr"]
        }

    def cleanup_orphans(self):
        """オーファンコンテナ・ボリュームの定期GC (設計書 8.4 浄化)"""
        containers = self.client.containers.list(all=True, filters={"label": "brownie_task_id"})
        for c in containers:
            if c.status != "running":
                logger.debug(f"Removing orphan container: {c.id}")
                try:
                    c.remove()
                except Exception:
                    pass
        
        self.client.volumes.prune()
