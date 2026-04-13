import subprocess
import json
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

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
