import asyncio
import datetime
import os
import sys
from typing import Any, Dict

# プロジェクトルートをパスに追加
sys.path.append(os.getcwd())

from src.mcp_server.manager import MCPServerManager


async def safe_call_tool(client, tool_name: str, arguments: Dict[str, Any]) -> str:
    """ツールを安全に呼び出し、結果をテキストで返す。"""
    if not client:
        return "Error: Client not connected"
    
    try:
        # セッション確立待ち
        for _ in range(10):
            if client.session:
                break
            await asyncio.sleep(0.5)
            
        result = await client.call_tool(tool_name, arguments)
        
        # CallToolResult からテキストを抽出
        if hasattr(result, "content") and isinstance(result.content, list):
            return "\n".join([c.text for c in result.content if hasattr(c, "text")])
        return str(result)
    except Exception as e:
        return f"Execution error in tool '{tool_name}': {e}"

async def main():
    project_root = os.getcwd()
    config_path = "config/config.yaml"
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"docs/analysis/REPORT_{timestamp}.md"
    
    # ユーザー設定（デフォルト）
    user_id = 501
    group_id = 20
    
    print(f"🚀 Starting Comprehensive Analysis: {project_root}")
    print(f"📄 Report output: {report_path}")
    
    report_content = [
        "# BROWNIE Comprehensive Analysis Report",
        f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Target:** `{project_root}`",
        "\n---\n"
    ]
    
    async with MCPServerManager(project_root, config_path) as manager:
        # 1. サーバー起動
        print("Initializing Core Servers...")
        workspace = await manager.start_workspace_server(project_root, project_root, user_id, group_id)
        
        # 2. 静的解析 (Semgrep)
        print("Running Static Analysis (Semgrep)...")
        semgrep_res = await safe_call_tool(workspace, "run_semgrep", {})
        report_content.extend(["## 1. Static Analysis (Semgrep)", "```json", semgrep_res, "```", "\n"])
        
        # 3. セキュリティスキャン (Bandit)
        print("Running Security Scan (Bandit)...")
        await manager.provision_servers(["security_analyzer"])
        sec_client = manager.plugin_clients["security_analyzer"]
        sec_res = await safe_call_tool(sec_client, "analyze_security", {"directory": "."})
        report_content.extend(["## 2. Security Scan (Bandit)", "```", sec_res, "```", "\n"])
        
        # 4. 依存関係監査
        print("Auditing Dependencies...")
        await manager.provision_servers(["dep_audit"])
        dep_client = manager.plugin_clients["dep_audit"]
        dep_res = await safe_call_tool(dep_client, "audit_dependencies", {})
        report_content.extend(["## 3. Dependency Audit", "```", dep_res, "```", "\n"])
        
        # 5. リポジトリ集約 (Repomix)
        print("Generating Global Context (Repomix)...")
        await manager.provision_servers(["context_aggregator"])
        agg_client = manager.plugin_clients["context_aggregator"]
        agg_res = await safe_call_tool(agg_client, "run_repomix_discovery", {
            "repo_path": ".",
            "exclude_patterns": ["*.log", ".git/**", ".brwn/**", "docs/analysis/**"]
        })
        # 集約結果は巨大になる可能性があるため、最初の1000文字のみ表示し、詳細は別ファイルへのリンクとする
        preview = agg_res[:1000] + "\n...(truncated)" if len(agg_res) > 1000 else agg_res
        report_content.extend(["## 4. Repository Context (Repomix Preview)", "```markdown", preview, "```", "\n"])
        
    # レポートの書き出し
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_content))
        
    print(f"✅ Analysis Completed! Total {len(report_content)} sections written.")

if __name__ == "__main__":
    asyncio.run(main())
