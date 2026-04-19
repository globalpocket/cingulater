import os

from loguru import logger
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend, RedisScheduleSource

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "brownie_secure_pw")
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"

logger.info(f"Connecting Taskiq Broker to Redis at {REDIS_HOST}:{REDIS_PORT}")

# 結果バックエンドとスケジュールソースの設定
result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
schedule_source = RedisScheduleSource(REDIS_URL)

# Redis ブローカーの初期化
broker = ListQueueBroker(url=REDIS_URL).with_result_backend(result_backend)
