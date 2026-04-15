import asyncio
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, os.getcwd())

async def test_meta_agent_tool():
    print("🚀 Meta-Agent (ワークフロー自己生成) の検証を開始します...")

    # SandboxManager と MCP Tool の準備 (Mock 的な初期化)
    from src.core.sandbox_manager import SandboxManager
    import src.mcp_server.workspace_server as ws
    
    repo_path = os.getcwd()
    # テスト用に現在のユーザー権限で Sandbox を動作させる
    ws._sandbox = SandboxManager(os.getuid(), os.getgid())
    ws._sandbox.set_workspace_root(repo_path)

    # 1. ワークフロー生成ツールの呼び出し
    workflow_name = "daily_todo_scan"
    description = "リポジトリ内の TODO コメントを抽出してレポートする毎朝9時のワークフロー"
    steps = [
        {
            "node_name": "extract",
            "prompt_content": "リポジトリ全体を検索し、'TODO' または 'FIXME' を含む行を抽出してください。",
            "next": "report"
        },
        {
            "node_name": "report",
            "prompt_content": "抽出されたリストを整理し、優先度順に並べたレポートを作成してください。",
            "next": "END"
        }
    ]
    triggers = [{"type": "cron", "value": "0 9 * * *"}]

    print(f"Creating workflow: {workflow_name}...")
    result = await ws.create_dynamic_workflow(
        workflow_name=workflow_name,
        description=description,
        steps=steps,
        triggers=triggers
    )
    print(f"Tool Result: {result}")

    # 2. ファイルの書き出し確認
    wf_base = Path(repo_path) / ".brwn" / "workflows"
    expected_files = [
        wf_base / f"{workflow_name}.yaml",
        wf_base / "node_extract.md",
        wf_base / "node_report.md"
    ]

    for f_path in expected_files:
        if f_path.exists():
            print(f"✅ ファイルを確認: {f_path.relative_to(repo_path)}")
            # 内容のチラ見
            content = f_path.read_text(encoding="utf-8")
            print(f"   (Content Size: {len(content)} bytes)")
        else:
            print(f"❌ ファイルが見つかりません: {f_path.relative_to(repo_path)}")
            sys.exit(1)

    print("\n✨ Meta-Agent 機能の検証が正常に完了しました。")

if __name__ == "__main__":
    asyncio.run(test_meta_agent_tool())
