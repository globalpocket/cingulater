import asyncio
import logging
import os
import sys
import signal
import json
import time
from dotenv import load_dotenv

# .env ファイルの読み込み (設計書 11.2 補足)
load_dotenv()

# プロジェクトルートをパスに追加 (設計書 3.2 補足)
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.core.agent import CoderAgent  # noqa: E402
from src.workspace.sandbox import SandboxManager  # noqa: E402
from src.version import get_build_id  # noqa: E402

# 1. ログ設定
from logging.handlers import RotatingFileHandler  # noqa: E402

log_file = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "logs", "brownie.log")
)
os.makedirs(os.path.dirname(log_file), exist_ok=True)
log_level = logging.DEBUG if os.environ.get("BROWNIE_DEBUG") == "1" else logging.INFO

root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Set standard formatter
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Suppress noise from external libraries
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("docker").setLevel(logging.INFO)
logging.getLogger("aiosqlite").setLevel(logging.INFO)

# Create console handler with INFO level
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# Create file handler with rotation (Harden: 5MB x 3 backups instead of 10x5)
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger("brownie.main")
logger.info(f"Logging initialized. Level: {log_level}, File: {log_file}")


class BrownieApp:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.orchestrator = Orchestrator(config_path)
        self.stop_event = asyncio.Event()

    async def run(self):
        """メインプロセスの実行 (設計書 3.2: 生存信号送信・LLM死活監視)"""
        logger.info(f"Starting Brownie Main Process (Build: {get_build_id()})...")
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
                                "build": get_build_id(),
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


if __name__ == "__main__":
    config_file = os.getenv("BROWNIE_CONFIG", "config/config.yaml")
    app = BrownieApp(config_file)
    asyncio.run(app.run())
