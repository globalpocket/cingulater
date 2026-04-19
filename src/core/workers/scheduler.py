from loguru import logger

from src.core.config import get_settings
from src.core.workers.pool import schedule_source
from src.core.workers.tasks import (
    llm_health_check_task,
    master_trigger_dispatcher,
    poll_mentions_task,
    resource_monitor_task,
)


async def setup_schedules():
    """スケジュールタスクの登録を行う"""
    settings = get_settings()

    # 1. メンション監視
    await poll_mentions_task.kiq().schedule(
        schedule_source,
        cron=f"*/{settings.agent.polling_interval_sec} * * * * *",
    )

    # 2. LLM ヘルスチェック (1分)
    await llm_health_check_task.kiq().schedule(schedule_source, cron="* * * * *")

    # 3. リソース監視 (30秒)
    await resource_monitor_task.kiq().schedule(schedule_source, cron="*/30 * * * * *")

    # 4. マスタートリガー (1分)
    await master_trigger_dispatcher.kiq().schedule(schedule_source, cron="* * * * *")

    logger.info("All periodic tasks registered in Taskiq Scheduler source.")
