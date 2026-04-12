import os
import logging
from huey import FileHuey

logger = logging.getLogger(__name__)

STORAGE_DIR = os.path.join("/Users/satoshitanaka/Documents/brownie", ".brwn", "huey_files")

if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    logger.info(f"Created Huey storage directory: {STORAGE_DIR}")

huey = FileHuey(path=STORAGE_DIR)
