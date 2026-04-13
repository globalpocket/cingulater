import os
import logging
from huey import RedisHuey

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")

huey = RedisHuey('brownie-tasks', host=REDIS_HOST, port=REDIS_PORT)
