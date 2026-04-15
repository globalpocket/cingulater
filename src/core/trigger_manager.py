import logging
from datetime import datetime
from typing import Any, Dict, List

try:
    from croniter import croniter
except ImportError:
    croniter = None

logger = logging.getLogger("brownie.trigger_manager")

class WorkflowTriggerManager:
    """
    ワークフローのトリガー（cron 等）を評価し、実行タイミングを判定する管理クラス。
    """

    def __init__(self):
        pass

    def check_cron_trigger(self, cron_expr: str, now: datetime) -> bool:
        """
        指定された cron 式が現在時刻（now）において実行されるべきか判定する。
        """
        if croniter is None:
            # A案の採用が予定されているが、インストール前の保護
            logger.warning("croniter is not installed. Cron triggers will be skipped.")
            return False
            
        try:
            # 1分単位の精度で評価（秒とマイクロ秒は無視）
            base_time = now.replace(second=0, microsecond=0)
            return croniter.match(cron_expr, base_time)
        except Exception as e:
            logger.error(f"Error evaluating cron expression '{cron_expr}': {e}")
            return False

    def get_due_workflows(
        self, tools_metadata: Dict[str, Any], now: datetime
    ) -> List[Dict[str, Any]]:
        """
        登録されたワークフローの中から、実行期限が来ているものをリストアップする。
        """
        due_list = []
        # tools_metadata は WorkflowRegistry._tools を想定 (Dict[str, WorkflowTool])
        for name, tool in tools_metadata.items():
            triggers = getattr(tool, "triggers", [])
            if not triggers:
                continue
            
            for trigger in triggers:
                if not isinstance(trigger, dict):
                    continue
                
                t_type = trigger.get("type")
                if t_type == "cron":
                    expr = trigger.get("value")
                    if expr and self.check_cron_trigger(expr, now):
                        due_list.append({
                            "workflow_name": name,
                            "trigger_type": "cron",
                            "schedule": expr
                        })
                        # 1つのワークフローで複数のトリガーがマッチしても、1回の周期では1度だけ実行
                        break
        return due_list
