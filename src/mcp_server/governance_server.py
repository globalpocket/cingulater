import logging
import sys
from typing import Optional

from fastmcp import FastMCP

# ロギング設定
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("governance_server")

# FastMCP サーバーの初期化
mcp = FastMCP("Governance")

# --- ツール定義 ---

@mcp.tool()
async def generate_ringi_sho(
    task_id: str,
    status: str,
    has_changes: bool,
    topic_branch: str,
    test_results_stdout: Optional[str] = None
) -> str:
    """
    タスクの実行結果をもとに、GitHub に投稿するための「稟議書 (Ringi-sho)」を
    生成します。
    """
    logger.info(f"Generating Ringi-sho for task: {task_id}")

    test_output = (
        test_results_stdout if test_results_stdout else "No test output available."
    )
    
    next_action_msg = (
        "プルリクエストを作成します。" if has_changes else "タスクを完了報告します。"
    )
    report = f"""## ⚖️ Brownie 実行稟議書 (Ringi-sho)

### 📊 実行サマリー
- **タスクID**: `{task_id}`
- **修正の有無**: {"✅ あり" if has_changes else "ℹ️ なし (調査のみ)"}
- **トピックブランチ**: `{topic_branch}`

### 🧪 検証結果 (Sandbox)
```text
{test_output}
```

### 🛠 次のアクション
承認（ `/approve` ）が得られた場合、{next_action_msg}
"""
    return report

if __name__ == "__main__":
    mcp.run(transport="stdio")
