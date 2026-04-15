from typing import Dict, Any, Optional
import os
from src.core.state_manager import TaskState
from src.core.workers.tasks import repair_task
from src.core.agent import GitHubClientWrapper
from src.utils.config_loader import get_footer

async def governance_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 4: Governance & Fail-Safe
    実行失敗時は修復ワーカーをキックし、成功時は稟議書（Ringi-sho）を GitHub に投稿して中断する。
    """
    print(f"--- Phase 4: Governance & Ringi ({state['task_id']}) ---")
    
    # 実行失敗かつ修復がまだの場合
    if state.get("status") == "Execution_Failed" and not state.get("ringi_document"):
        print(f"Execution failed. Enqueuing repair_task for {state['task_id']}...")
        repair_task(state['task_id'], state.get("error_context", "Unknown error"))
        return {
            "status": "Waiting_Repair",
            "history": [{"node": "governance", "status": "repair_enqueued"}]
        }

    # すでに Ringi を投稿済みかチェック (二重投稿防止)
    if not state.get("governance_decision") and state.get("status") != "WaitingForApproval":
        # 稟議書の作成
        ringi_content = state.get("ringi_document")
        if not ringi_content:
            has_changes = state.get("has_changes", False)
            branch = state.get("topic_branch", "None")
            test_results = state.get("test_results", {})
            
            ringi_content = f"""## ⚖️ Brownie 実行稟議書 (Ringi-sho)

### 📊 実行サマリー
- **タスクID**: `{state['task_id']}`
- **修正の有無**: {"✅ あり" if has_changes else "ℹ️ なし (調査のみ)"}
- **トピックブランチ**: `{branch}`

### 🧪 検証結果 (Sandbox)
```text
{test_results.get('stdout', 'No test output available.') if test_results else 'N/A'}
```

### 🛠 次のアクション
承認（ `/approve` ）が得られた場合、{"プルリクエストを作成します。" if has_changes else "タスクを完了報告します。"}
"""
        
        # GitHub に投稿
        gh = GitHubClientWrapper(os.getenv("GITHUB_TOKEN", ""))
        # task_id は "repo#number" 形式
        repo_name = state['task_id'].split("#")[0]
        issue_number = int(state['task_id'].split("#")[1])
        
        await gh.post_comment(repo_name, issue_number, ringi_content + get_footer())
        
        return {
            "status": "WaitingForApproval",
            "ringi_document": ringi_content,
            "history": [{"node": "governance", "status": "ringi_posted"}]
        }

    # 承認済みかどうかをチェック
    if state.get("governance_decision") == "Approve":
        return {
            "status": "Approved",
            "history": [{"node": "governance", "status": "approved"}]
        }
    
    return {
        "status": "WaitingForApproval",
        "history": [{"node": "governance", "status": "waiting_decision"}]
    }
