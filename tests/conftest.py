import pytest
import os

@pytest.fixture(autouse=True)
def set_test_env():
    """テスト実行中は環境変数をテスト用に固定する"""
    os.environ["CINGULATER_CONFIG"] = "dummy_config.yaml"
    yield
    os.environ.pop("CINGULATER_CONFIG", None)
