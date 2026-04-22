import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import computed_field
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
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
        "auto_wiki": 3,
    }
    repositories: List[str] = ["globalpocket/brownie"]
    exclude_repositories: List[str] = []
    allowed_commands: List[str] = [
        "git",
        "ruff",
        "bandit",
        "semgrep",
        "npx",
        "python",
        "uv",
        "gh",
        "pytest",
    ]


class LLMSettings(BaseSettings):
    orchestrator_endpoint: str = "http://localhost:8080/v1"
    executor_endpoint: str = "http://localhost:8081/v1"
    timeout_sec: int = 300
    tokenizer: str = "auto"
    max_context_tokens: int = 12000
    model_dir: str = "~/.local/share/brownie/models"
    models: Dict[str, str] = {
        "orchestrator": "mlx-community/gemma-4-26b-a4b-it-4bit",
        "executor": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
    }


class WorkspaceSettings(BaseSettings):
    sandbox_user_id: int = 1000
    sandbox_group_id: int = 1000
    lfs_enabled: bool = True
    base_dir: str = "~/.local/share/brownie/workspaces"


class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""


class ChromaSettings(BaseSettings):
    host: str = "localhost"
    port: int = 8000


class GitHubSettings(BaseSettings):
    token: str = ""


class DatabaseSettings(BaseSettings):
    db_path: str = "~/.local/share/brownie/brownie.db"
    memory_path: str = "~/.local/share/brownie/vector_db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__", env_prefix="BROWNIE_", extra="ignore"
    )

    debug: bool = False
    agent: AgentSettings = AgentSettings()
    llm: LLMSettings = LLMSettings()
    workspace: WorkspaceSettings = WorkspaceSettings()
    redis: RedisSettings = RedisSettings()
    chroma: ChromaSettings = ChromaSettings()
    github: GitHubSettings = GitHubSettings()
    database: DatabaseSettings = DatabaseSettings()

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

        # 生の環境変数をマップするカスタムソース
        class RawEnvSettingsSource(EnvSettingsSource):
            def prepare_field_value(self, field_name, field, value, value_is_complex):
                # GITHUB_TOKEN -> github.token への手動マッピング
                if field_name == "github":
                    token = os.getenv("GITHUB_TOKEN") or os.getenv(
                        "GITHUB_PERSONAL_ACCESS_TOKEN"
                    )
                    if token:
                        return {"token": token}
                if field_name == "redis":
                    return {
                        "host": os.getenv("REDIS_HOST", "localhost"),
                        "port": int(os.getenv("REDIS_PORT", "6379")),
                        "password": os.getenv("REDIS_PASSWORD", ""),
                    }
                if field_name == "chroma":
                    return {
                        "host": os.getenv("CHROMADB_HOST", "localhost"),
                        "port": int(os.getenv("CHROMADB_PORT", "8000")),
                    }
                if field_name == "debug":
                    return os.getenv("BROWNIE_DEBUG") == "1"
                return super().prepare_field_value(
                    field_name, field, value, value_is_complex
                )

        raw_env = RawEnvSettingsSource(settings_cls)
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / os.getenv("BROWNIE_CONFIG", "config/config.yaml")
        magic_path = config_path.parent / "magicvalues.yaml"

        sources = [init_settings, env_settings, raw_env]

        # magicvalues.yaml (優先度低)
        if magic_path.exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=magic_path))

        # config.yaml
        if config_path.exists():
            sources.append(
                YamlConfigSettingsSource(settings_cls, yaml_file=config_path)
            )

        return tuple(sources)

    @computed_field
    @property
    def build_id(self) -> str:
        """現在のGitコミットハッシュを取得し、ビルドIDとして返す"""
        try:
            project_root = Path(__file__).parent.parent.parent
            git_path = shutil.which("git") or "git"
            # 引数は完全にリテラルであり、インジェクションの余地はないため nosec 付与
            build_id = (
                subprocess.check_output(
                    [git_path, "rev-parse", "--short", "HEAD"],
                    cwd=project_root,
                    stderr=subprocess.DEVNULL,
                )  # nosec B603
                .decode("utf-8")
                .strip()
            )
            return build_id
        except (subprocess.SubprocessError, OSError):
            # Git が未インストール、または .git がない場合はバージョンを返す
            return VERSION

    def validate_connectivity(self) -> None:
        """重要なインフラストラクチャへの接続性を検証します。"""
        from loguru import logger

        # 1. Redis 接続確認
        try:
            import redis

            logger.debug(f"Validating Redis connectivity to {self.redis.host}...")
            r = redis.Redis(
                host=self.redis.host,
                port=self.redis.port,
                db=self.redis.db,
                password=self.redis.password,
                socket_connect_timeout=2,
            )
            r.ping()
            logger.info("✅ Redis connectivity verified.")
        except (ImportError, redis.ConnectionError, redis.TimeoutError) as e:
            logger.exception("Unexpected error during Redis connectivity check")
            raise RuntimeError(f"Critical infrastructure (Redis) is unreachable: {e}")

        # 2. ChromaDB 接続確認
        try:
            import httpx

            url = f"http://{self.chroma.host}:{self.chroma.port}/api/v1/heartbeat"
            logger.debug(f"Validating ChromaDB connectivity to {url}...")
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                logger.info("✅ ChromaDB connectivity verified.")
            else:
                raise RuntimeError(f"ChromaDB returned status code {resp.status_code}")
        except (httpx.HTTPError, RuntimeError) as e:
            logger.error(f"❌ ChromaDB connectivity failed: {e}")
            raise RuntimeError(
                f"Critical infrastructure (ChromaDB) is unreachable: {e}"
            )

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
