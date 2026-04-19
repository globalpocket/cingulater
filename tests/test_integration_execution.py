import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# プロジェクトルートを追加
sys.path.append(str(Path(__file__).parent.parent))

from src.core.orchestrator import Orchestrator
from src.core.graph.builder import compile_workflow

async def main():
    print("Testing Workflow Execution flow (T4-2)...")
    
    # 1. Orchestrator インスタンス化 (ワークフローロード含む)
    config_path = "config/config.yaml"
    orch = Orchestrator(config_path)
    
    # 2. LLM と外部クライアントのモック
    dummy_wf_result = {
        "results": {
            "analyze": {
                "status": "pending",
                "draft_comment": "この指示で進めてもよろしいでしょうか？",
                "intent_summary": "テスト目標",
                "evaluation_axes": ["機能A"],
                "required_mcp_servers": ["workspace"]
            }
        }
    }
    
    # interpreter ワークフロー callable を差し替え
    orch.dynamic_workflows["interpreter"] = AsyncMock(return_value=dummy_wf_result)
    
    # 外部クライアントモック
    orch.gh_client = AsyncMock()
    orch.gh_client.get_issue.return_value = {"state": "open"}
    orch.gh_client.post_comment = AsyncMock()
    
    # 必要メソッドのモック
    orch._wait_for_llm_ready = AsyncMock()
    
    # Persistence client モック
    orch.mcp_manager = MagicMock()
    orch.mcp_manager.persistence_client = AsyncMock()
    
    # State Manager ダミー
    class DummyContextManager:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __getattr__(self, name): return AsyncMock()

    orch.state_manager = DummyContextManager()
    
    # ワークフローのコンパイル (本来は start() 中で行われる)
    orch._workflow_app = compile_workflow(
        workflows=orch.dynamic_workflows,
        mcp_manager=orch.mcp_manager,
        checkpointer=None # メモリ保存
    )

    # get_state / update_state もモック (DBアクセスの代わりにメモリで保持)
    mem_state = {"reported_nodes": []}
    async def mock_get_state(tid): return mem_state
    async def mock_update_state(tid, values, as_node=None):
        mem_state.update(values)
        return mem_state
    
    orch.get_state = AsyncMock(side_effect=mock_get_state)
    orch.update_state = AsyncMock(side_effect=mock_update_state)

    # テスト入力
    task_id = "test-repo#2"
    repo_name = "test-repo"
    issue_number = 2
    payload = {"instruction": "アプリのUIを改善して"}

    print("Running _execute_task...")

    try:
        await orch._execute_task(task_id, repo_name, issue_number, payload)
    except Exception as e:
        print(f"❌ FAILED: Error during _execute_task: {e}")
        import traceback
        traceback.print_exc()
        return

    # 3. 検証
    if orch.gh_client.post_comment.called:
        print("✅ SUCCESS: Workflow executed and GitHub comment triggered.")
        for call in orch.gh_client.post_comment.call_args_list:
            args, _ = call
            if "🔍 意図の確認と提案" in args[2]:
                print(f"   Success content found: {args[2][:80]}...")
                break
        else:
             print("❌ FAILED: Comment content was not what was expected.")
    else:
        print("❌ FAILED: GitHub comment was never posted.")

if __name__ == "__main__":
    asyncio.run(main())
