# src/core/base.py

class TaskAbortedException(Exception):  # noqa: N818
    """ユーザーによって Issue がクローズされた場合に投げられる例外"""

    pass
