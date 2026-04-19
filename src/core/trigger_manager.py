from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import yaml

from loguru import logger

try:
    from croniter import croniter
except ImportError:
    croniter = None


class WorkflowTriggerManager:
    """
    ワークフローのトリガー（cron, events）を評価し、動的にルーティングする管理クラス。
    (Phase 9: 深層ドメイン抽出により純粋なディスパッチャへと昇華)
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path(os.getcwd())

    async def handle_event(self, event_type: str, payload: Dict[str, Any]):
        """
        イベントを受け取り、規約（Convention）または各ワークフロー内の定義に基づいてアクションを実行する。
        """
        logger.info(f"Handling event: {event_type}")

        # 1. 規約ベースのルーティング (Convention over Configuration)
        # イベント名と同名のワークフローが存在すれば、それを実行する
        from src.core.orchestrator import global_orchestrator

        if global_orchestrator and event_type in global_orchestrator.dynamic_workflows:
            from src.core.workers.tasks import execute_workflow_task

            logger.info(
                f"✨ Convention matched: Routing event '{event_type}' directly to its namesake workflow."
            )
            await execute_workflow_task.kiq(event_type, input_data=payload)
            return

        # 2. 各ワークフロー定義に埋め込まれたトリガーに基づくルーティング
        if not global_orchestrator:
            return

        for wf_name, tool in global_orchestrator.dynamic_workflows.items():
            triggers = getattr(tool, "triggers", [])
            for trigger in triggers:
                # 'event' タイプのトリガーであり、かつイベント名（value）が一致するか確認
                if trigger.get("type") != "event":
                    continue
                if trigger.get("value") != event_type:
                    continue

                # 条件評価 (任意)
                condition_str = trigger.get("condition", "True")
                try:
                    is_matched = eval(
                        condition_str, {"payload": payload, "__builtins__": {}}
                    )
                except Exception as e:
                    logger.error(f"Failed to evaluate condition '{condition_str}' in {wf_name}: {e}")
                    continue

                if not is_matched:
                    continue

                from src.core.workers.tasks import execute_workflow_task
                logger.info(
                    f"🚀 Routing event '{event_type}' to workflow '{wf_name}' via internal trigger."
                )
                await execute_workflow_task.kiq(wf_name, input_data=payload)


    # --- Legacy Compat / Cron Support ---
    def check_cron_trigger(self, cron_expr: str, now: datetime) -> bool:
        if croniter is None:
            return False
        try:
            base_time = now.replace(second=0, microsecond=0)
            return croniter.match(cron_expr, base_time)
        except Exception:
            return False

    def get_due_workflows(
        self, tools_metadata: Dict[str, Any], now: datetime
    ) -> List[Dict[str, Any]]:
        # 既存の master_trigger_dispatcher から呼び出される互換レイヤー
        due_list = []
        for name, tool in tools_metadata.items():
            triggers = getattr(tool, "triggers", [])
            for trigger in triggers:
                if trigger.get("type") == "cron":
                    expr = trigger.get("value")
                    if expr and self.check_cron_trigger(expr, now):
                        due_list.append(
                            {
                                "workflow_name": name,
                                "trigger_type": "cron",
                                "schedule": expr,
                            }
                        )
                        break
        return due_list
