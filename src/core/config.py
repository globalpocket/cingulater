import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0--alpha"

class AgentSettings(BaseSettings):
    polling_interval_sec: int = 30
    max_auto_retries: int = 30
    max_llm_retries: int = 5
    max_history_steps: int = 15
    oob_webhook_url: str = ""
    queue_ux_notification: bool = True
    inference_priority: Dict[str, int] = {
        "manual_issue": 1,
        "review_comment": 2,
        "auto_wiki": 3
    }
    repositories: List[str] = ["globalpocket/brownie"]
    exclude_repositories: List[str] = []

class LLMSettings(BaseSettings):
    planner_endpoint: str = "http://localhost:8080/v1"
    executor_endpoint: str = "http://localhost:8081/v1"
    timeout_sec: int = 300
    tokenizer: str = "auto"
    max_context_tokens: int = 12000
    model_dir: str = "~/.local/share/brownie/models"
    models: Dict[str, str] = {
        "planner": "mlx-community/gemma-4-26b-a4b-it-4bit",
        "executor": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    }

class WorkspaceSettings(BaseSettings):
    sandbox_user_id: int = 1000
    sandbox_group_id: int = 1000
    lfs_enabled: bool = True
    base_dir: str = "~/.local/share/brownie/workspaces"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="BROWNIE_",
        extra="ignore"
    )

    agent: AgentSettings = AgentSettings()
    llm: LLMSettings = LLMSettings()
    workspace: WorkspaceSettings = WorkspaceSettings()

    @computed_field
    @property
    def build_id(self) -> str:
        """現在のGitコミットハッシュを取得し、ビルドIDとして返す"""
        try:
            # プロジェクトルートを取得 (src/core/config.py から見て 2つ上)
            project_root = Path(__file__).parent.parent.parent
            build_id = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], 
                cwd=project_root,
                stderr=subprocess.DEVNULL
            ).decode("utf-8").strip()
            return build_id
        except Exception:
            return VERSION

    @property
    def footer(self) -> str:
        """GitHubコメント用の標準フッターを生成する"""
        return f"\n\n---\n> Built from: `{self.build_id}`"

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Settings":
        """
        YAMLファイルから設定を読み込み、Settingsオブジェクトを生成します。
        環境変数による上書き、magicvalues.yaml のマージも行います。
        """
        if config_path is None:
            config_path = os.getenv("BROWNIE_CONFIG", "config/config.yaml")

        # プロジェクトルートからの相対パス解決
        if not os.path.isabs(config_path):
            project_root = Path(__file__).parent.parent.parent
            config_path = str(project_root / config_path)

        # 1. config.yaml の読み込み
        init_data = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data:
                    init_data.update(yaml_data)

        # 2. magicvalues.yaml の読み込みとマージ
        magic_path = os.path.join(os.path.dirname(config_path), "magicvalues.yaml")
        if os.path.exists(magic_path):
            with open(magic_path, "r", encoding="utf-8") as f:
                magic_data = yaml.safe_load(f)
                if magic_data:
                    _deep_merge(magic_data, init_data)

        # 3. Pydantic-settings に YAML データを初期値として渡す
        # これにより、環境変数が YAML データを上書きする (Pydantic の標準挙動)
        return cls(**init_data)

def _deep_merge(source: dict, destination: dict):
    for key, value in source.items():
        if isinstance(value, dict) and key in destination and isinstance(destination[key], dict):
            _deep_merge(value, destination[key])
        else:
            destination[key] = value

_settings: Optional[Settings] = None

def get_settings(config_path: Optional[str] = None) -> Settings:
    """Settings のシングルトンインスタンスを取得します。"""
    global _settings
    if _settings is None or config_path:
        _settings = Settings.load(config_path)
    return _settings
