import logging
from typing import Optional, List, Literal
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# --- 型定義 (Core から分散) ---

class RingiDocument(BaseModel):
    """
    稟議書 (Phase 4)
    """
    summary: str = Field(..., description="発生した事象の概要")
    impact_analysis: str = Field(..., description="影響範囲の分析")
    proposed_fix: str = Field(..., description="具体的な修正案")
    risk_assessment: str = Field(..., description="リスク評価")

# --- サーバー定義 ---

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("governance_server")

mcp = FastMCP("Governance")

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
    mcp.run()
