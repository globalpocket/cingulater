"""
BROWNIE Resource Monitor MCP Server
==============================
システムリソース（CPU/メモリ）およびプロセスの状態を監視する MCP サーバー。
Orchestrator や Worker プロセスのリソース使用状況を監視し、安全な実行を支援する。
"""

import os
import time
from typing import Any, Dict, Optional

import psutil

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)

# --- サーバーインスタンスの生成 ---
# 名前を BrownieResourceMonitor に統一
mcp = create_mcp_server("BrownieResourceMonitor")


class ResourceGuardianLogic:
    """ResourceGuardian のロジックをカプセル化したクラス"""

    def __init__(self, memory_limit_gb: float = 24.0, cpu_stall_threshold: float = 5.0):
        self.memory_limit_gb = memory_limit_gb
        self.cpu_stall_threshold = cpu_stall_threshold  # 5% 以下をアイドルと見なす

    def get_system_metrics(self) -> Dict[str, Any]:
        """システム全体のメモリとCPU状態を取得"""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        return {
            "total_gb": mem.total / (1024**3),
            "available_gb": mem.available / (1024**3),
            "used_pct": mem.percent,
            "cpu_pct": cpu,
        }

    def get_process_metrics(self, pid: int) -> Dict[str, Any]:
        """指定されたプロセスのメモリとCPU状態を取得（子プロセス含む）"""
        try:
            proc = psutil.Process(pid)
            total_rss = proc.memory_info().rss
            children = proc.children(recursive=True)
            for child in children:
                try:
                    total_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            return {
                "pid": pid,
                "process_rss_gb": total_rss / (1024**3),
                "cpu_pct": proc.cpu_percent(interval=None),
            }
        except Exception as e:
            logger.error(f"Failed to get metrics for PID {pid}: {e}")
            return {"pid": pid, "error": str(e)}

    def is_resource_safe(self, min_available_gb: float = 4.0) -> bool:
        """システム全体のメモリが安全な範囲内か判断する"""
        metrics = self.get_system_metrics()
        if metrics["available_gb"] < min_available_gb:
            logger.warning(
                "CRITICAL: Low system memory! "
                f"Available: {metrics['available_gb']:.2f}GB"
            )
            return False
        return True

    def check_for_stall(
        self, pid: int, last_progress_time: float, timeout_sec: int = 300
    ) -> bool:
        """
        プロセスの進捗が止まっているか、リソース状況を交えて複合的に判断する。
        """
        idle_time = time.time() - last_progress_time

        if idle_time < timeout_sec:
            return False

        # タイムアウト超過時、CPU 負荷を確認
        metrics = self.get_process_metrics(pid)
        cpu_usage = metrics.get("cpu_pct", 0.0)

        if cpu_usage > self.cpu_stall_threshold:
            # 負荷があるなら推論中と判断し、さらに 5 分猶予
            if idle_time < timeout_sec + 300:
                logger.info(
                    "Task heartbeat stopped, but CPU is active "
                    f"({cpu_usage}%). Assuming activity in progress."
                )
                return False
            else:
                logger.warning(
                    f"Task active for a long time without progress ({idle_time}s). "
                    "Forcefully deciding as stall."
                )
                return True

        logger.error(f"STALL DETECTED: No progress for {idle_time}s and CPU is idle.")
        return True


# --- グローバルロジックインスタンス ---
# デフォルト値で初期化
_logic = ResourceGuardianLogic()


@mcp.tool()
@mcp_tool_errorhandler
async def get_system_resources() -> Dict[str, Any]:
    """システム全体のメモリとCPUの状態を取得します。"""
    return _logic.get_system_metrics()


@mcp.tool()
@mcp_tool_errorhandler
async def get_process_resources(pid: Optional[int] = None) -> Dict[str, Any]:
    """指定されたプロセスのメモリとCPU状態を取得します。
    PID 指定がない場合は、親プロセス（監視対象）を対象にします。
    """
    target_pid = pid if pid is not None else os.getppid()
    return _logic.get_process_metrics(target_pid)


@mcp.tool()
@mcp_tool_errorhandler
async def is_system_safe(min_available_gb: float = 4.0) -> bool:
    """システムのリソース（空きメモリ）が安全な範囲内にあるかを確認します。"""
    return _logic.is_resource_safe(min_available_gb)


@mcp.tool()
@mcp_tool_errorhandler
async def check_stall(
    last_heartbeat: float, timeout_sec: int = 300, pid: Optional[int] = None
) -> bool:
    """プロセスの進捗状況から、ストールしているかを判定します。"""
    target_pid = pid if pid is not None else os.getppid()
    return _logic.check_for_stall(target_pid, last_heartbeat, timeout_sec)


if __name__ == "__main__":
    mcp.run(transport="stdio")
