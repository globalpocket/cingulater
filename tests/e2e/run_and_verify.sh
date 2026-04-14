#!/bin/bash
export PYTHONPATH=$PYTHONPATH:$(pwd)
export GITHUB_TOKEN=$(grep GITHUB_TOKEN .env | cut -d '=' -f2)

echo "--- 1. Resetting all processes ---"
ps aux | grep -v grep | grep -E "src/main.py|huey" | awk '{print $2}' | xargs kill -9 2>/dev/null || true
rm -rf .brwn/huey_files/*

echo "--- 2. Starting Orchestrator ---"
nohup .venv/bin/python -u src/main.py > logs/orchestrator_prod.log 2>&1 &

echo "--- 3. Starting Worker ---"
nohup .venv/bin/huey_consumer src.core.workers.tasks.huey -w 1 -k thread > logs/worker_prod.log 2>&1 &

sleep 10

echo "--- 4. Injecting Final Success Trigger ---"
.venv/bin/python -c "
import asyncio, os, sys
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.getcwd())
from src.core.worker_pool import WorkerPool
async def force():
    pool = WorkerPool(os.getcwd())
    await pool.add_task(
        task_id='globalpocket/brownie-sampleproject#1',
        priority=1,
        repo_name='globalpocket/brownie-sampleproject',
        issue_number=1,
        comment_id='final_match_fact',
        payload={'instruction': '事実復旧：AIエージェント自律稼働テスト', 'status': 'InProgress'}
    )
asyncio.run(force())"

echo "--- 5. Waiting for AI execution (60s) ---"
sleep 60

echo "--- 6. Extracting Final Proof from Log ---"
grep -aE "STARTING ASYNC EXECUTION|COMPLETED EXECUTION|Successfully posted|Workflow successfully initialized" logs/worker_prod.log
