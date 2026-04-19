import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# プロジェクトルートをパスに追加
sys.path.append(str(Path(__file__).parent.parent))

from src.core.orchestrator import Orchestrator

async def main():
    print("Testing Orchestrator initialization and mention flow...")
    
    # 1. Orchestrator インスタンス化
    # config はデフォルト (config/config.yaml) を使用
    config_path = "config/config.yaml"
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.")
        return

    try:
        orch = Orchestrator(config_path)
    except Exception as e:
        print(f"❌ FAILED: Initialization error: {e}")
        import traceback
        traceback.print_exc()
        return

    # 2. クライアントをモック
    orch.gh_client = AsyncMock()
    orch.gh_client.get_mentions_to_process.return_value = [
        {
            "repo_name": "test-repo",
            "number": 1,
            "comment_id": "test-comment-id",
            "body": "Hello Brownie!",
            "updated_at": "2026-04-20T00:00:00Z"
        }
    ]
    orch.gh_client.mark_issue_notifications_as_read = AsyncMock()
    
    # mcp_manager をモック
    orch.mcp_manager = MagicMock()
    
    # persistence_client を AsyncMock に設定
    orch.mcp_manager.persistence_client = AsyncMock()
    orch.mcp_manager.persistence_client.call_tool.return_value = "NEW"
    
    # worker_controller_client を AsyncMock に設定
    orch.mcp_manager.worker_controller_client = AsyncMock()
    
    # settings モック (exclude_repositories 回避用)
    orch.settings.agent.exclude_repositories = []

    print("Running _poll_mentions...")
    try:
        await orch._poll_mentions()
    except Exception as e:
        print(f"❌ FAILED: Error during _poll_mentions: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 3. 検証
    # _queue_task が呼ばれ、最終的に worker_controller_client.call_tool が呼ばれたか
    if orch.mcp_manager.worker_controller_client.call_tool.called:
        print("✅ SUCCESS: Mention discovered and task enqueued successfully.")
        args, kwargs = orch.mcp_manager.worker_controller_client.call_tool.call_args
        print(f"   Task details: {kwargs}")
    else:
        print("❌ FAILED: Mention processing flow was interrupted.")

if __name__ == "__main__":
    asyncio.run(main())
