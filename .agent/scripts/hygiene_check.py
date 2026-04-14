#!/usr/bin/env python3
import subprocess
import sys
import os

def check_hygiene():
    """AI エージェントが残した可能性のある不要なプロセスをチェックする"""
    print("🧹 Checking for agent-spawned zombie processes...")
    
    # チェック対象のプロセス名パターン
    patterns = [
        "headless_shell",
        "Chromium",
        "chrome --headless",
        "playwright",
        "puppeteer",
        ".brwn/scripts", # 自身以外のスクリプト
    ]
    
    found_zombies = []
    
    try:
        # ps コマンドでプロセス一覧を取得
        ps_output = subprocess.check_output(["ps", "-eo", "pid,args"]).decode()
        for line in ps_output.splitlines():
            line = line.strip()
            # 自身（このスクリプト）と grep は除外
            if str(os.getpid()) in line:
                continue
                
            for pattern in patterns:
                if pattern in line:
                    found_zombies.append(line)
                    break
                    
    except Exception as e:
        print(f"❌ Error during process scanning: {e}")
        return False

    if found_zombies:
        print("\n⚠️  [HYGIENE WARNING] Found orphaned processes:")
        for z in found_zombies:
            print(f"  - {z}")
        print("\nSuggestion: Run `/cleanup` or `pkill` to remove these before finishing.")
        return False
    else:
        print("✅ [HYGIENE OK] No agent-spawned zombies found.")
        return True

if __name__ == "__main__":
    success = check_hygiene()
    if not success:
        sys.exit(1)
    sys.exit(0)
