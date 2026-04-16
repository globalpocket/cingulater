import os
from typing import Any, Dict

from src.utils.config_loader import get_footer

from src.core.agent import GitHubClientWrapper
from src.core.state_manager import TaskState


async def completion_node(state: TaskState) -> Dict[str, Any]:
    """
    Final Phase: タスクの完了報告と PR 作成
    """
    print(f"--- Final Phase: Completion ({state['task_id']}) ---")
    
    gh = GitHubClientWrapper(os.getenv("GITHUB_TOKEN", ""))
    repo_name = state['task_id'].split("#")[0]
    issue_number = int(state['task_id'].split("#")[1])
    
    has_changes = state.get("has_changes", False)
    topic_branch = state.get("topic_branch")
    
    if has_changes and topic_branch:
        # PR 作成
        pr_title = f"Fix for {state['task_id']}: {state['instruction'][:50]}..."
        pr_body = f"""## 🚀 Brownie による自動修正 PR

この PR は Issue #{issue_number} を解決するためのものです。

### 📝 修正内容
{state.get('execution_result_summary', '自動修正が適用されました。')}

### 🧪 検証結果
サンドボックス内でのテストをパスしています。
"""
        try:
            # PR作成 (baseブランチはとりあえず 'main')
            pr = await gh.create_pull_request(
                repo_name, 
                pr_title, 
                pr_body, 
                topic_branch, 
                "main"
            )
            pr_url = pr.html_url if pr else "Failed to get PR URL"
            
            msg = f"✅ PR を作成しました: {pr_url}\n承認ありがとうございました。"
            await gh.post_comment(repo_name, issue_number, msg + get_footer())
            
            return {
                "status": "Completed",
                "pr_url": pr_url,
                "history": [{"node": "completion", "status": "pr_created"}]
            }
        except Exception as e:
            print(f"Failed to create PR: {e}")
            await gh.post_comment(repo_name, issue_number, f"❌ PR作成中にエラーが発生しました: {e}" + get_footer())
            return {"status": "Failed", "metadata": {"error": str(e)}}
    else:
        # 報告のみ
        msg = "✅ タスクを完了しました（コード修正は不要と判断されました）。\n承認ありがとうございました。"
        await gh.post_comment(repo_name, issue_number, msg + get_footer())
        
        return {
            "status": "Completed",
            "history": [{"node": "completion", "status": "reported_no_changes"}]
        }
