import os

from huey import RedisHuey
from loguru import logger

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")

huey = RedisHuey('brownie-tasks', host=REDIS_HOST, port=REDIS_PORT)
