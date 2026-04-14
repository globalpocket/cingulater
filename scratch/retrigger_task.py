import asyncio
import os
import sys

# プロジェクトルートをパスに追加
sys.path.insert(0, os.getcwd())

from src.core.workers.tasks import analysis_task

async def main():
    task_id = "globalpocket/brownie-sampleproject#2"
    repo_name = "globalpocket/brownie-sampleproject"
    issue_number = 2
    
    # ターゲットとなるメンション情報
    payload = {
        "repo_name": repo_name,
        "number": issue_number,
        "comment_id": "4241394263",
        "body": "@globalpocket-sub お願いします", # 元のコメント内容に近いものを設定
        "updated_at": "2026-04-14T15:00:00Z" # 新しい日付にして強制検知
    }
    
    print(f"Manual Retrigger: Submitting task {task_id} (Comment: 4241394263) to Huey...")
    analysis_task.delay(task_id, repo_name, issue_number, payload)
    print("✅ Task successfully queued via Huey.")

if __name__ == "__main__":
    asyncio.run(main())
