import pytest
from src.utils.cmd_helper import run_command, run_command_async

def test_run_command_allowed():
    # git はデフォルトの許可リストに含まれている
    result = run_command(["git", "--version"])
    assert result.exit_code == 0
    assert "git version" in result.stdout.lower()

def test_run_command_not_allowed():
    # rm など、許可リストに含まれていないコマンドは ValueError になるはず
    with pytest.raises(ValueError, match="not in the allowed white list"):
        run_command(["rm", "--version"])

def test_run_command_null_byte():
    # ヌルバイトを含む引数は拒否されるはず
    with pytest.raises(ValueError, match="null byte"):
        run_command(["git", "version\0"])

@pytest.mark.asyncio
async def test_run_command_async_allowed():
    result = await run_command_async(["git", "--version"])
    assert result.exit_code == 0
