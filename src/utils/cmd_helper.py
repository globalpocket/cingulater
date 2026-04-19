import asyncio
import dataclasses
import shlex
import subprocess
from typing import Dict, List, Optional, Union

from loguru import logger


@dataclasses.dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    combined: str


def run_command(
    args: Union[str, List[str]],
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = False,
) -> CommandResult:
    """
    同期的にコマンドを実行し、結果を返す。
    セキュリティ確保のため、shell=True は内部で False に強制され、
    文字列引数は shlex.split で安全に分割されます。
    """
    # 安全のため shell=True を無効化し、shlex で分割する
    final_args = shlex.split(args) if isinstance(args, str) else args
    cmd_str = args if isinstance(args, str) else " ".join(args)
    logger.debug(f"Running command: {cmd_str}")

    try:
        result = subprocess.run(
            final_args,
            cwd=cwd,
            timeout=timeout,
            env=env,
            shell=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            logger.warning(
                f"Command failed (exit {result.returncode}): {cmd_str}\nStderr: {stderr}"
            )
        else:
            logger.debug(f"Command success: {cmd_str}")

        return CommandResult(
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            combined=(stdout + "\n" + stderr).strip(),
        )
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out ({timeout}s): {cmd_str}")
        return CommandResult(
            exit_code=-1,
            stdout=e.stdout.decode() if e.stdout else "",
            stderr="TimeoutExpired",
            combined="TimeoutExpired",
        )
    except Exception as e:
        logger.error(f"Command execution error: {cmd_str} -> {e}")
        return CommandResult(exit_code=-2, stdout="", stderr=str(e), combined=str(e))


async def run_command_async(
    args: Union[str, List[str]],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = False,
) -> CommandResult:
    """
    非同期（asyncio）でコマンドを実行し、結果を返す。
    セキュリティ確保のため、常に create_subprocess_exec を使用します。
    """
    final_args = shlex.split(args) if isinstance(args, str) else args
    cmd_str = args if isinstance(args, str) else " ".join(args)
    logger.debug(f"Running command (async): {cmd_str}")

    try:
        # 常に exec 版を使用し、shell 経由の実行を避ける
        process = await asyncio.create_subprocess_exec(
            *final_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        stdout_b, stderr_b = await process.communicate()
        stdout = stdout_b.decode().strip()
        stderr = stderr_b.decode().strip()
        exit_code = process.returncode if process.returncode is not None else -1

        if exit_code != 0:
            logger.warning(
                f"Async command failed (exit {exit_code}): {cmd_str}\nStderr: {stderr}"
            )
        else:
            logger.debug(f"Async command success: {cmd_str}")

        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            combined=(stdout + "\n" + stderr).strip(),
        )
    except Exception as e:
        logger.error(f"Async command execution error: {cmd_str} -> {e}")
        return CommandResult(exit_code=-2, stdout="", stderr=str(e), combined=str(e))
