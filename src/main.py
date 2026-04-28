import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

# .env ファイルの読み込み
load_dotenv()

import typer
from typing_extensions import Annotated

from core.config import get_settings
from core.orchestrator import Orchestrator

# プロジェクトルートをパスに追加
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# 1. ログ設定
log_file = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "logs", "brownie.log")
)
os.makedirs(os.path.dirname(log_file), exist_ok=True)
log_level = "DEBUG" if os.environ.get("BROWNIE_DEBUG") == "1" else "INFO"


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging():
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.remove()
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level=log_level, format=log_format)
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
        "{name}:{function}:{line} - {message}"
    )
    logger.add(
        log_file, rotation="5 MB", retention="3 days", level="DEBUG", format=file_format
    )


setup_logging()


class BrownieApp:
    def __init__(self, config_file: str):
        self.settings = get_settings(config_file)
        self.orchestrator = Orchestrator(config_file)
        self.stop_event = asyncio.Event()

    async def run(self):
        """メインプロセスの実行"""
        logger.info(
            f"Starting Brownie Main Process (Build: {self.settings.build_id})..."
        )

        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda: asyncio.create_task(self.shutdown()))

        try:
            # 1. Orchestrator の開始（MCP ゲートウェイ接続を含む）
            await self.orchestrator.start()

            # 2. 定期的な生存信号（Watchdog向け）
            survival_task = asyncio.create_task(self._send_survival_signals())

            # 3. 停止シグナルを待機
            await self.stop_event.wait()
            
            survival_task.cancel()

        except Exception:
            logger.exception("Fatal error in main process")
        finally:
            # 4. Orchestrator の安全な停止
            await self.orchestrator.shutdown()
            logger.info("Brownie Main Process stopped.")

    async def _send_survival_signals(self):
        pid = os.getpid()
        data_dir = Path.home() / ".local" / "share" / "brownie"
        data_dir.mkdir(parents=True, exist_ok=True)
        signal_file = str(data_dir / "survival.signal")
        try:
            while not self.stop_event.is_set():
                with open(signal_file, "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "pid": pid,
                                "timestamp": time.time(),
                                "build": self.settings.build_id,
                            }
                        )
                    )
                await asyncio.sleep(30)
        finally:
            if os.path.exists(signal_file):
                os.remove(signal_file)

    async def shutdown(self):
        """シャットダウンシグナルの受信"""
        logger.info("Shutdown signal received. Closing Brownie...")
        self.stop_event.set()


def main(
    config: Annotated[
        Optional[str], typer.Option("--config", "-c", help="Path to config yaml file")
    ] = None,
):
    """BROWNIE: Autonomous AI Coding Agent 🚀"""
    config_file = config or os.getenv("BROWNIE_CONFIG", "config/config.yaml")

    if hasattr(os, "setpgrp"):
        os.setpgrp()

    try:
        app = BrownieApp(config_file)
        asyncio.run(app.run())
    except Exception:
        logger.exception(
            "FATAL ERROR: System is crashing. Killing all related processes."
        )
        if hasattr(os, "killpg"):
            try:
                os.killpg(0, signal.SIGTERM)
            except Exception as e:
                logger.error(f"Failed to kill process group: {e}")
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)