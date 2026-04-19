import asyncio
from unittest.mock import MagicMock, patch

import pytest
from src.workspace.context import WorkspaceContext
from src.workspace.sandbox import SandboxManager


@pytest.fixture
def sandbox():
    # Docker 接続がなくてもモックで動くように設定
    with patch("docker.from_env"):
        manager = SandboxManager(user_id=1000, group_id=1000)
        manager.context = WorkspaceContext(root_path="/tmp/fake_repo")
        return manager


@pytest.mark.asyncio
async def test_lint_code_calls_run_command(sandbox):
    # LinterEngine をモック化
    with patch("src.workspace.sandbox.LinterEngine") as mock_linter_class:
        mock_linter = mock_linter_class.return_value
        mock_linter.get_lint_command.return_value = "ruff check ."

        # run_command をモック化
        sandbox.run_command = MagicMock()
        sandbox.run_command.return_value = asyncio.Future()
        sandbox.run_command.return_value.set_result(
            {"exit_code": 0, "stdout": "All clear", "stderr": ""}
        )

        result = await sandbox.lint_code(".")

        assert "ruff check ." in str(sandbox.run_command.call_args)
        assert "All clear" in result
        assert "Status: 0" in result


@pytest.mark.asyncio
async def test_format_code_calls_run_command(sandbox):
    with patch("src.workspace.sandbox.LinterEngine") as mock_linter_class:
        mock_linter = mock_linter_class.return_value
        mock_linter.get_format_command.return_value = "black ."

        sandbox.run_command = MagicMock()
        sandbox.run_command.return_value = asyncio.Future()
        sandbox.run_command.return_value.set_result(
            {"exit_code": 0, "stdout": "Formatted", "stderr": ""}
        )

        result = await sandbox.format_code(".")

        assert "black ." in str(sandbox.run_command.call_args)
        assert "Formatted" in result


@pytest.mark.asyncio
async def test_scan_security_calls_run_command(sandbox):
    with patch("src.workspace.sandbox.LinterEngine") as mock_linter_class:
        mock_linter = mock_linter_class.return_value
        mock_linter.get_security_command.return_value = "bandit -r ."

        sandbox.run_command = MagicMock()
        sandbox.run_command.return_value = asyncio.Future()
        sandbox.run_command.return_value.set_result(
            {"exit_code": 0, "stdout": "Secure", "stderr": ""}
        )

        result = await sandbox.scan_security(".")

        assert "bandit -r ." in str(sandbox.run_command.call_args)
        assert "Secure" in result
