import asyncio
import os
import sys
sys.path.append(os.getcwd())

from src.core.orchestrator import Orchestrator

async def run():
    print("--- Emergency Direct Execution Starting ---")
    config_path = "config/config.yaml"
    
    # 既存プロセスを一度掃除
    os.system("ps aux | grep -v grep | grep -E 'src/main.py|huey' | awk '{print $2}' | xargs kill -9 2>/dev/null")
    
    orch = Orchestrator(config_path)
    
    task_id = "globalpocket/brownie-sampleproject#1"
    repo_name = "globalpocket/brownie-sampleproject"
    issue_number = 1
    
    # payload を偽装して「分析開始」を GitHub に投稿させる
    # (Orchestrator._execute_task は内部でエージェントを回してコメントを投稿するはず)
    payload = {
        "instruction": "事実確認のための強制実行テスト",
        "status": "Resurrected"
    }
    
    print(f"Executing task for {task_id}...")
    try:
        # 30秒だけ回して、最初のコメント投稿 (Phase 0) が行われるか確認
        await asyncio.wait_for(orch._execute_task(task_id, repo_name, issue_number, payload), timeout=60)
    except asyncio.TimeoutError:
        print("Execution timed out, but check GitHub for comments.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
