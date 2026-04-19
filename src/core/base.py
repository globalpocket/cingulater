from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.core.orchestrator import Orchestrator

# グローバルなオーケストレーター参照
# 循環参照を避けるため、ここに配置する
global_orchestrator: Optional["Orchestrator"] = None

class TaskAbortedException(Exception):  # noqa: N818
    """ユーザーによって Issue がクローズされた場合に投げられる例外"""
    pass

def get_global_orchestrator() -> Optional["Orchestrator"]:
    return global_orchestrator

def set_global_orchestrator(orch: "Orchestrator"):
    global global_orchestrator
    global_orchestrator = orch
