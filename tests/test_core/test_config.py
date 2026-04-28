import pytest
from unittest.mock import patch, mock_open
from core.config import get_settings, Settings

def test_default_settings():
    """設定ファイルがない場合はデフォルト値が使われることの確認"""
    with patch("pathlib.Path.exists", return_value=False):
        settings = get_settings()
        assert settings.agent.max_retries == 3
        assert settings.llm.timeout_sec == 120
        assert settings.workspace.sandbox_user == "brownie_sandbox"
        assert settings.llm.models["interlocutor"] == "mlx-community/gemma-4-26b-a4b-it-4bit"

def test_load_valid_yaml():
    """正常なYAMLファイルが読み込まれ、設定が上書きされることの確認"""
    mock_yaml = """
agent:
  max_retries: 10
llm:
  timeout_sec: 60
    """
    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=mock_yaml)):
            settings = Settings.load("dummy.yaml")
            assert settings.agent.max_retries == 10
            assert settings.llm.timeout_sec == 60
            assert settings.workspace.sandbox_user == "brownie_sandbox" # 記述がないものはデフォルト

@patch("core.config.logger.error")
def test_load_invalid_yaml(mock_logger_error):
    """壊れたYAMLファイルや読み込みエラー時のフォールバック確認"""
    with patch("builtins.open", side_effect=Exception("Permission denied")):
        settings = Settings.load("dummy.yaml")
        assert settings.agent.max_retries == 3  # デフォルトに戻る
        
        # loguru の logger.error が正しく呼ばれ、意図したメッセージが含まれているか検証
        mock_logger_error.assert_called_once()
        assert "Failed to load config" in mock_logger_error.call_args[0][0]