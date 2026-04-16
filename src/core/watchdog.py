#!/usr/bin/env python3
import time
import os
import sys
import signal
import subprocess
from loguru import logger
from logging.handlers import RotatingFileHandler
import shutil
import glob
from typing import Optional

# プロジェクトルートをパスに追加
base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(base_dir)

# ログディレクトリの作成
log_dir = os.path.join(base_dir, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "brownie.log")

# ログ設定 (ファイルと標準出力の両方に出力)
log_level = logging.DEBUG if os.environ.get("BROWNIE_DEBUG") == "1" else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("brownie.watchdog")

class Watchdog:
    def __init__(self, main_script: str, survival_file: str):
        self.main_script = main_script
        self.survival_file = survival_file
        self.process: Optional[subprocess.Popen] = None
        self.last_survival_time = time.time()
        self.max_crashes = 5
        self.crash_count = 0
        self.is_running = True
        
        # --- 追加: ホットリロード用の監視設定 ---
        self.watch_dirs = [
            os.path.join(base_dir, "src"),
            os.path.join(base_dir, "config"),
            os.path.join(base_dir, "workflows"),
            os.path.join(base_dir, ".brwn", "workflows")
        ]
        self.p_root = base_dir
        self.file_mtimes = self._get_all_mtimes()
        # ----------------------------------------

        # シグナルハンドラの設定 (設計書 4.2 運用監視)
        signal.signal(signal.SIGINT, self._handle_exit_signal)
        signal.signal(signal.SIGTERM, self._handle_exit_signal)

    def _handle_exit_signal(self, signum, frame):
        """終了シグナル受信時の処理"""
        logger.info(f"Received signal {signum}. Shutting down Brownie...")
        self.is_running = False
        if self.process:
            logger.info("Terminating main process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Main process did not terminate. Force killing...")
                self.process.kill()
        
        if os.path.exists(self.survival_file):
            os.remove(self.survival_file)
        
        sys.exit(0)

    # --- 追加: ファイル更新日時取得メソッド ---
    def _get_all_mtimes(self):
        mtimes = {}
        for d in self.watch_dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(('.py', '.yaml', '.yml', '.md')):
                        p = os.path.join(root, f)
                        try:
                            mtimes[p] = os.path.getmtime(p)
                        except FileNotFoundError:
                            pass
        return mtimes

    def _check_file_changes(self):
        """ファイルの変更を検知してプロセスを再起動する"""
        current_mtimes = self._get_all_mtimes()
        for p, mtime in current_mtimes.items():
            if p not in self.file_mtimes or self.file_mtimes[p] < mtime:
                logger.info(f"Hot-reload triggered: File changed -> {p}")
                self.file_mtimes = current_mtimes
                if self.process:
                    self.process.terminate() # メインプロセスをキルして再起動を誘発
                return True
        return False
    # ------------------------------------------

    def start(self):
        """Watchdogの実行 (設計書 3.2)"""
        logger.info("Starting Brownie Watchdog...")
        
        while self.is_running:
            # 1. メインプロセスの起動・監視
            if self.process is None or self.process.poll() is not None:
                self._handle_restart()
            
            # 2. 生存信号の確認
            self._check_survival()
            
            # --- 追加: ファイル変更検知の呼び出し ---
            self._check_file_changes()
            
            if not self.is_running:
                break
                
            time.sleep(15) # 反応速度を上げるために 30s -> 15s に短縮推奨

    def _handle_restart(self):
        """プロセス再起動と CrashLoopBackOff"""
        if self.crash_count >= self.max_crashes:
            logger.error("Too many crashes! System stopping.")
            self.is_running = False
            return
        
        # 指数バックオフ
        wait_time = min(2 ** self.crash_count, 60)
        if self.crash_count > 0:
            logger.info(f"Waiting {wait_time}s before restart...")
            time.sleep(wait_time)
            
        logger.info(f"Restarting main process (Attempt: {self.crash_count + 1})...")
        
        venv_python = os.path.join(base_dir, ".venv", "bin", "python")
        # メインプロセスを起動 (親の死を検知できるようにする等、将来的な拡張の余地を残す)
        self.process = subprocess.Popen(
            [venv_python, self.main_script],
            cwd=base_dir
        )
        
        self.crash_count += 1
        self.last_survival_time = time.time()

    def _check_survival(self):
        """生存信号の確認"""
        try:
            if os.path.exists(self.survival_file):
                mtime = os.path.getmtime(self.survival_file)
                if mtime > self.last_survival_time:
                    self.last_survival_time = mtime
                    if time.time() - self.last_survival_time < 60:
                        self.crash_count = 0
            
            # 1時間以上生存信号がなければハングアップとみなす
            # (GitHub APIのBackoffが40分程度になるケースがあるため、余裕を持たせる)
            if time.time() - self.last_survival_time > 3600:
                logger.warning("Main process seems hung (No survival signal for 1 hour). Killing it...")
                if self.process:
                    self.process.terminate()
        except Exception as e:
            logger.error(f"Survival check error: {e}")

    def _monitor_resources(self):
        """リソース監視"""
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024**3)
        if free_gb < 2: # 閾値を少し下げて 2GB
            logger.error(f"Disk space critically low: {free_gb:.2f} GB left!")

if __name__ == "__main__":
    import fcntl
    from pathlib import Path
    
    # ロックファイルの取得
    data_dir = Path.home() / ".local" / "share" / "brownie"
    lock_path = data_dir / "brownie.lock"
    pid_file = data_dir / "brownie.pid"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    def try_lock(path):
        f = open(path, "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            return None

    lock_f = try_lock(lock_path)
    if lock_f is None:
        # ロックが取れない場合、実際にプロセスが生きているか確認
        is_stale = True
        if pid_file.exists():
            try:
                with open(pid_file, "r") as pf:
                    pid = int(pf.read().strip())
                    os.kill(pid, 0) # 生存確認
                    is_stale = False
                    print(f"Error: Another Watchdog is already running (PID: {pid}).")
            except (ValueError, ProcessLookupError):
                pass
        
        if is_stale:
            # プロセスはいないのにロックがある = Stale Lock
            print("⚠️ Stale lock detected in watchdog. Cleaning up...")
            if lock_path.exists():
                try: os.remove(lock_path)
                except: pass
            lock_f = try_lock(lock_path)
            if lock_f is None:
                print("Error: Could not acquire lock even after cleanup.")
                sys.exit(1)
        else:
            sys.exit(1)

    script_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    dog = Watchdog(script_path, "/tmp/brownie_survival.signal")
    dog.start()
