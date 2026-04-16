import os
import shutil

import yaml


def reset():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    print("🧹 Cleaning up system state...")
    
    # 1. configを読み込んでパスを特定
    config_path = os.path.join(base_dir, "config", "config.yaml")
    db_path = "~/.local/share/brownie/brownie.db"
    mem_path = "~/.local/share/brownie/vector_db"
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                cfg = yaml.safe_load(f)
                db_path = cfg.get('database', {}).get('db_path', db_path)
                mem_path = cfg.get('database', {}).get('memory_path', mem_path)
        except Exception as e:
            print(f"Warning: Could not parse config.yaml: {e}")

    # 2. データベース削除
    checkpoint_path = os.path.join(base_dir, ".brwn", "checkpoints.db")
    for p in [db_path, mem_path, checkpoint_path]:
        full_p = os.path.expanduser(p)
        if os.path.exists(full_p):
            print(f"Removing {full_p}...")
            try:
                if os.path.isdir(full_p):
                    shutil.rmtree(full_p)
                else:
                    os.remove(full_p)
            except Exception as e:
                print(f"Error removing {full_p}: {e}")

    # 3. ログ削除
    log_dir = os.path.join(base_dir, "logs")
    if os.path.exists(log_dir):
        print("Cleaning logs...")
        for f in os.listdir(log_dir):
            if f.endswith(".log") or f.endswith(".log.1"):
                try:
                    os.remove(os.path.join(log_dir, f))
                except Exception as e:
                    print(f"Error removing log {f}: {e}")

    # 4. 一時ワークスペース削除
    tmp_ws = "/tmp/brownie_workspace"
    if os.path.exists(tmp_ws):
        print(f"Removing temporary workspace: {tmp_ws}")
        try:
            shutil.rmtree(tmp_ws)
        except Exception as e:
            print(f"Error removing workspace: {e}")

    print("✨ System reset complete. Ready for a fresh start!")

if __name__ == "__main__":
    reset()
