import os
import psutil
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class ResourceGuardian:
    """システムのメモリとCPUリソース、およびプロセスの状態を監視し、
    AI実行の安全性を判断・確保する「資源の守護者」クラス。"""
    
    def __init__(self, memory_limit_gb: float = 24.0, cpu_stall_threshold: float = 5.0):
        self.memory_limit_gb = memory_limit_gb
        self.cpu_stall_threshold = cpu_stall_threshold  # 5% 以下をアイドルと見なす
        self.process = psutil.Process(os.getpid())

    @classmethod
    def get_system_metrics(cls) -> Dict[str, Any]:
        """システム全体のメモリとCPU状態を取得"""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        return {
            "total_gb": mem.total / (1024**3),
            "available_gb": mem.available / (1024**3),
            "used_pct": mem.percent,
            "cpu_pct": cpu
        }

    def get_worker_metrics(self) -> Dict[str, Any]:
        """現在のワーカープロセスのメモリとCPU状態を取得（子プロセス含む）"""
        try:
            # 自身と子プロセスのメモリを合算 (MLX サーバーなども含まれる可能性があるため)
            total_rss = self.process.memory_info().rss
            children = self.process.children(recursive=True)
            for child in children:
                try:
                    total_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            return {
                "pid": self.process.pid,
                "worker_rss_gb": total_rss / (1024**3),
                "cpu_pct": self.process.cpu_percent(interval=None)
            }
        except Exception as e:
            logger.error(f"Failed to get worker metrics: {e}")
            return {"pid": os.getpid(), "error": str(e)}

    def is_resource_safe(self) -> bool:
        """システム全体のメモリが安全な範囲内か判断する"""
        metrics = self.get_system_metrics()
        # 32GB Mac で空きが 4GB 以下なら危険（モデル 1 つ分すら怪しい）
        if metrics["available_gb"] < 4.0:
            logger.warning(f"CRITICAL: Low system memory! Available: {metrics['available_gb']:.2f}GB")
            return False
        return True

    def check_for_stall(self, last_progress_time: float, timeout_sec: int = 300) -> bool:
        """
        プロセスの進捗が止まっているか、リソース状況を交えて複合的に判断する。
        - 進捗なし & CPU使用率が低い & タイムアウト超過 = ストール
        - 進捗なし & CPU使用率が高い = 推論中とみなして猶予
        """
        import time
        idle_time = time.time() - last_progress_time
        
        if idle_time < timeout_sec:
            return False
            
        # タイムアウト超過時、CPU 負荷を確認
        metrics = self.get_worker_metrics()
        cpu_usage = metrics.get("cpu_pct", 0.0)
        
        if cpu_usage > self.cpu_stall_threshold:
            # 負荷があるなら推論中と判断し、さらに 5 分猶予
            if idle_time < timeout_sec + 300:
                logger.info(f"Task heartbeat stopped, but CPU is active ({cpu_usage}%). Assuming inference in progress.")
                return False
            else:
                logger.warning(f"Task active for a long time without progress ({idle_time}s). Forcefully deciding as stall.")
                return True
        
        logger.error(f"STALL DETECTED: No progress for {idle_time}s and CPU is idle.")
        return True
