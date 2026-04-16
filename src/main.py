import asyncio
from loguru import logger
import os
import sys
import signal
import json
import time
from typing import Optional
from dotenv import load_dotenv

# .env ファイルの読み込み (設計書 11.2 補足)
load_dotenv()

import typer
from typing_extensions import Annotated

# プロジェクトルートをパスに追加 (設計書 3.2 補足)
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.core.agent import CoderAgent  # noqa: E402
from src.core.sandbox_manager import SandboxManager  # noqa: E402
from src.core.config import get_settings  # noqa: E402

from loguru import logger

# 1. ログ設定
log_file = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "logs", "brownie.log")
)
os.makedirs(os.path.dirname(log_file), exist_ok=True)
log_level = "DEBUG" if os.environ.get("BROWNIE_DEBUG") == "1" else "INFO"

# Loguru の初期化 (stderr とファイルの両方に出力)
# 標準 logging を Loguru にリダイレクトするためのハンドラ設定
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

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logging():
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # 既存のハンドラをクリアして Loguru で再構築
    logger.remove()
    logger.add(
        sys.stderr, 
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(
        log_file,
        rotation="5 MB",
        retention="3 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )

setup_logging()
logger.info(f"Loguru initialized. Level: {log_level}, File: {log_file}")


class BrownieApp:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.orchestrator = Orchestrator(config_path)
        self.stop_event = asyncio.Event()

    async def run(self):
        """メインプロセスの実行 (設計書 3.2: 生存信号送信・LLM死活監視)"""
        logger.info(f"Starting Brownie Main Process (Build: {self.settings.build_id})...")
        logger.info(
            f"  - Loaded Agent from: {CoderAgent.__module__} in {os.path.abspath(CoderAgent.__module__.replace('.', '/') + '.py')}"
        )
        logger.info(f"  - Loaded Orchestrator from: {Orchestrator.__module__}")
        logger.info(f"  - Loaded Sandbox from: {SandboxManager.__module__}")

        # 設計書に基づき、シグナルハンドラを設定
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda: asyncio.create_task(self.shutdown()))

        try:
            # 1. 起動
            orchestrator_task = asyncio.create_task(self.orchestrator.start())

            # 2. 定期的な生存信号（Watchdog向け）
            asyncio.create_task(self._send_survival_signals())

            # 3. 待機
            await self.stop_event.wait()

            # 4. 停止
            orchestrator_task.cancel()
            try:
                await orchestrator_task
            except asyncio.CancelledError:
                pass

        except Exception as e:
            logger.error(f"Fatal error in main process: {e}")
        finally:
            logger.info("Brownie Main Process stopped.")

    async def _send_survival_signals(self):
        """Watchdogへの生存信号の送信 (設計書 3.2: 生存信号)"""
        pid = os.getpid()
        signal_file = "/tmp/brownie_survival.signal"
        logger.info(f"Starting survival signal: {signal_file}")
        try:
            while not self.stop_event.is_set():
                # PID をファイル名に含め、内容も JSON 化して詳細情報を付与する
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
                logger.info(f"Removed survival signal: {signal_file}")

    async def shutdown(self):
        """シャットダウン処理"""
        logger.info("Shutting down Brownie...")
        self.stop_event.set()


def main(
    config: Annotated[Optional[str], typer.Option("--config", "-c", help="Path to config yaml file")] = None
):
    """
    BROWNIE: Autonomous AI Coding Agent 🚀
    """
    config_file = config or os.getenv("BROWNIE_CONFIG", "config/config.yaml")
    app = BrownieApp(config_file)
    asyncio.run(app.run())

if __name__ == "__main__":
    typer.run(main)
