import pytest
import os

@pytest.fixture(autouse=True)
def set_test_env():
    """テスト実行中は環境変数をテスト用に固定する"""
    os.environ["BROWNIE_CONFIG"] = "dummy_config.yaml"
    yield
    os.environ.pop("BROWNIE_CONFIG", None)