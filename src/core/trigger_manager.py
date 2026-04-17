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
        self.routing_rules = self._load_routing_rules()

    def _load_routing_rules(self) -> List[Dict[str, Any]]:
        """workflows/events_routing.yaml から ECA ルールをロード"""
        routing_path = self.project_root / "workflows" / "events_routing.yaml"
        if not routing_path.exists():
            logger.warning(
                f"Routing rules not found at {routing_path}. Using empty rules."
            )
            return []

        try:
            with open(routing_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("events", [])
        except Exception as e:
            logger.error(f"Failed to load routing rules: {e}")
            return []

    async def handle_event(self, event_type: str, payload: Dict[str, Any]):
        """
        イベントを受け取り、規約（Convention）またはルールに基づいてアクションを実行する。
        (Phase 10: 設定より規約の実装)
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
            # 規約ベースの場合、ペイロードをそのまま input_data として渡す
            await execute_workflow_task.kiq(event_type, input_data=payload)
            return

        # 2. 明示的なルールベースのルーティング (Fallback to Configuration)
        for rule in self.routing_rules:
            if rule.get("type") != event_type:
                continue

            # 条件評価
            condition_str = rule.get("condition", "True")
            try:
                is_matched = eval(
                    condition_str, {"payload": payload, "__builtins__": {}}
                )
            except Exception as e:
                logger.error(f"Failed to evaluate condition '{condition_str}': {e}")
                continue

            if not is_matched:
                continue

            # アクション実行
            action = rule.get("action")
            if not action or action.get("type") != "run_workflow":
                continue

            wf_name = action.get("name")
            mapping = action.get("params_mapping", {})
            params = {}

            for target_key, expr in mapping.items():
                try:
                    params[target_key] = eval(
                        expr, {"payload": payload, "__builtins__": {}}
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to map param '{target_key}' with expr '{expr}': {e}"
                    )

            from src.core.workers.tasks import execute_workflow_task

            logger.info(
                f"🚀 Routing event '{event_type}' to workflow '{wf_name}' via explicit rule."
            )
            await execute_workflow_task.kiq(wf_name, input_data=params)

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
