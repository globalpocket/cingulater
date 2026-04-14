import os
import yaml
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_config = None

def get_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    設定ファイルを読み込み、シングルトンとして返します。
    """
    global _config
    if _config is not None:
        return _config

    if config_path is None:
        config_path = os.getenv("BROWNIE_CONFIG", "config/config.yaml")

    # プロジェクトルートからの相対パス解決
    if not os.path.isabs(config_path):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(project_root, config_path)

    logger.info(f"Loading configuration from {config_path}")
    try:
        with open(config_path, 'r') as f:
            _config = yaml.safe_load(f)
        
        # magicvalues.yaml による上書きロジックもここに集約可能だが
        # 一旦最小限の実装とする
        magic_path = os.path.join(os.path.dirname(config_path), "magicvalues.yaml")
        if os.path.exists(magic_path):
            with open(magic_path, 'r') as f:
                magic = yaml.safe_load(f)
                if magic:
                    _deep_merge(magic, _config)
                    logger.info("Magic values merged into config.")
                    
        return _config
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}

def _deep_merge(source, destination):
    for key, value in source.items():
        if isinstance(value, dict) and key in destination and isinstance(destination[key], dict):
            _deep_merge(value, destination[key])
        else:
            destination[key] = value
