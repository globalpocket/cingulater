import operator
import os
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import redis.asyncio as redis
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from loguru import logger


from src.core.types import TaskState


class StateManager:
    """
    LangGraph の状態管理（チェックポインタ）をラップするクラス
    """

    def __init__(self, db_path: Optional[str] = None):
        from src.core.config import get_settings

        self.settings = get_settings().redis
        self._pool: Optional[redis.ConnectionPool] = None
        self._saver: Optional[AsyncRedisSaver] = None

    @property
    def saver(self) -> Optional[AsyncRedisSaver]:
        """チェックポインタ（セーバー）を取得する"""
        return self._saver

    async def connect(self):
        """チェックポインタに接続する（外部ドライバやツール用）"""
        if not self._saver:
            logger.debug(
                "Connecting to Redis Checkpointer at "
                f"{self.settings.host}:{self.settings.port}"
            )
            # 認証パスワードを含めた URL を生成
            # 今回セットアップした redis-stack はデフォルトパスワードなし
            if self.settings.password:
                url = (
                    f"redis://:{self.settings.password}@"
                    f"{self.settings.host}:{self.settings.port}/{self.settings.db}"
                )
            else:
                url = f"redis://{self.settings.host}:{self.settings.port}/{self.settings.db}"
            
            self._saver = AsyncRedisSaver(redis_url=url)
        return self

    async def __aenter__(self):
        """async with ステートメントでチェックポインタを有効化する"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        async with ステートメント終了時にチェックポインタを閉じる
        """
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
            self._saver = None

    async def get_state_lightweight(self, thread_id: str) -> Dict[str, Any]:
        """
        グラフのコンパイルを行わずに、チェックポインタから直接最新状態を取得する。
        """
        if not self._saver:
            await self.connect()

        config = {"configurable": {"thread_id": thread_id}}
        state = await self._saver.aget(config)
        return state.values if state else {}
