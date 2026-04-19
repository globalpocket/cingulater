import ast
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
                f"✨ Convention matched: Routing event '{event_type}' "
                "directly to its namesake workflow."
            )
            await execute_workflow_task.kiq(event_type, input_data=payload)
            return

        # 2. 各ワークフロー定義に埋め込まれたトリガーに基づくルーティング
        if not global_orchestrator:
            return

        for wf_name, tool in global_orchestrator.dynamic_workflows.items():
            triggers = getattr(tool, "triggers", [])
            for trigger in triggers:
                # 'event' タイプのトリガーであり、かつイベント名（value）が
                # 一致するか確認
                if trigger.get("type") != "event":
                    continue
                if trigger.get("value") != event_type:
                    continue

                # 条件評価 (任意)
                condition_str = trigger.get("condition", "True")
                is_matched = self._safe_eval(condition_str, payload)
                if not is_matched:
                    continue

                from src.core.workers.tasks import execute_workflow_task

                logger.info(
                    f"🚀 Routing event '{event_type}' to workflow '{wf_name}' "
                    "via internal trigger."
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

    def _safe_eval(self, expr: str, payload: Dict[str, Any]) -> bool:
        """
        eval() を使わず、AST を再帰的に自身で評価する安全な評価器。
        """
        try:
            tree = ast.parse(expr, mode="eval")
            result = self._evaluate_ast_node(tree.body, payload)
            return bool(result)
        except Exception as e:
            logger.error(f"Safe eval failed for '{expr}': {e}")
            return False

    def _evaluate_ast_node(self, node: ast.AST, payload: Dict[str, Any]) -> Any:
        """AST ノードを再帰的に評価するヘルパーメソッド。
        許可された演算のみを処理する。
        """
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Name):
            if node.id == "payload":
                return payload
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            if node.id == "None":
                return None
            raise ValueError(f"Forbidden variable: {node.id}")
        elif isinstance(node, ast.UnaryOp):
            operand = self._evaluate_ast_node(node.operand, payload)
            if isinstance(node.op, ast.Not):
                return not operand
            raise ValueError(f"Forbidden unary op: {type(node.op).__name__}")
        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for v in node.values:
                    if not self._evaluate_ast_node(v, payload):
                        return False
                return True
            elif isinstance(node.op, ast.Or):
                for v in node.values:
                    if self._evaluate_ast_node(v, payload):
                        return True
                return False
        elif isinstance(node, ast.Compare):
            left = self._evaluate_ast_node(node.left, payload)
            for op, right_node in zip(node.ops, node.comparators):
                right = self._evaluate_ast_node(right_node, payload)
                if isinstance(op, ast.Eq):
                    if not (left == right):
                        return False
                elif isinstance(op, ast.NotEq):
                    if not (left != right):
                        return False
                elif isinstance(op, ast.Lt):
                    if not (left < right):
                        return False
                elif isinstance(op, ast.LtE):
                    if not (left <= right):
                        return False
                elif isinstance(op, ast.Gt):
                    if not (left > right):
                        return False
                elif isinstance(op, ast.GtE):
                    if not (left >= right):
                        return False
                elif isinstance(op, ast.In):
                    if left not in right:
                        return False
                elif isinstance(op, ast.NotIn):
                    if left in right:
                        return False
                else:
                    raise ValueError(f"Forbidden comparison op: {type(op).__name__}")
                left = right
            return True
        elif isinstance(node, ast.Subscript):
            value = self._evaluate_ast_node(node.value, payload)
            # Python 3.9+ 互換の Index 処理
            index_node = node.slice
            if hasattr(ast, "Index") and isinstance(index_node, ast.Index):
                index_node = index_node.value
            index = self._evaluate_ast_node(index_node, payload)
            return value[index]

        raise ValueError(f"Forbidden syntax: {type(node).__name__}")

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
