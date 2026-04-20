from loguru import logger
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend, RedisScheduleSource

from src.core.config import get_settings

settings = get_settings().redis
REDIS_HOST = settings.host
REDIS_PORT = settings.port
REDIS_PASSWORD = settings.password
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{settings.db}"

logger.info(f"Connecting Taskiq Broker to Redis at {REDIS_HOST}:{REDIS_PORT}")

# 結果バックエンドとスケジュールソースの設定
result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
schedule_source = RedisScheduleSource(REDIS_URL)

# Redis ブローカーの初期化
broker = ListQueueBroker(url=REDIS_URL).with_result_backend(result_backend)
