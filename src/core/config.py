import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """設定ソースの優先順位を定義（YAMLファイル > 環境変数）"""
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / os.getenv("BROWNIE_CONFIG", "config/config.yaml")
        magic_path = config_path.parent / "magicvalues.yaml"

        sources = [init_settings, env_settings]
        
        # magicvalues.yaml (優先度低)
        if magic_path.exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=magic_path))
        
        # config.yaml
        if config_path.exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=config_path))
            
        return tuple(sources)

    @computed_field
    @property
    def build_id(self) -> str:
        """現在のGitコミットハッシュを取得し、ビルドIDとして返す"""
        try:
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

_settings: Optional[Settings] = None

def get_settings(config_path: Optional[str] = None) -> Settings:
    """Settings のシングルトンインスタンスを取得します。"""
    global _settings
    if _settings is None or config_path:
        # Pydantic-Settings 2.x ではインスタンス化時に自動でソースが読み込まれる
        _settings = Settings()
    return _settings
