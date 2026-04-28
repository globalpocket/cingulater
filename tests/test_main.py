import pytest
from unittest.mock import patch
import main

def test_main_cli():
    """main コマンドが uvicorn.run を正しく呼び出しているかテスト"""
    with patch("main.uvicorn.run") as mock_run:
        # Typer コマンドの直接呼び出し
        main.main(host="127.0.0.1", port=9000, config="dummy.yaml")
        
        mock_run.assert_called_once_with(
            "api.server:app", 
            host="127.0.0.1", 
            port=9000, 
            log_level="info"
        )