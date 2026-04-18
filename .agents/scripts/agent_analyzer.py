#!/usr/bin/env python3
import asyncio
import datetime
import json
import os
import sys
from typing import Any, Dict, List

# プロジェクトルートをパスに追加
sys.path.append(os.getcwd())

from src.core.mcp_server_manager import MCPServerManager

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

async def analyze():
    project_root = os.getcwd()
    config_path = "config/config.yaml"
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"docs/analysis/AGENT_REPORT_{timestamp}.md"
    
    # ユーザー設定
    user_id = 1000 # config.yaml に合わせたデフォルト
    group_id = 1000
    
    print(f"🔍 [AGENT] Starting In-depth Analysis: {project_root}")
    
    report_sections = [
        f"# BROWNIE Agent Analysis Report - {timestamp}",
        f"- **Project Root:** `{project_root}`",
        f"- **Time:** {datetime.datetime.now().isoformat()}",
        "\n---"
    ]
    
    async with MCPServerManager(project_root, config_path) as manager:
        print("🚀 Initializing Workspace Server...")
        workspace = await manager.start_workspace_server(project_root, project_root, user_id, group_id)
        
        # 1. Semgrep解析
        print("📡 Running Semgrep...")
        semgrep_res = await safe_call_tool(workspace, "run_semgrep", {})
        report_sections.append("## 1. Static Analysis (Semgrep)")
        report_sections.append(f"```\n{semgrep_res}\n```\n")
        
        # 2. セキュリティスキャン (Bandit)
        print("🛡️ Running Security Scan (Bandit)...")
        sec_res = await safe_call_tool(workspace, "scan_security", {"path": "."})
        report_sections.append("## 2. Security Scan (Bandit)")
        report_sections.append(f"```\n{sec_res}\n```\n")
        
        # 3. コード品質 (Ruff/Format)
        print("✨ Checking Code Hygiene (Ruff)...")
        lint_res = await safe_call_tool(workspace, "lint_code", {"path": "."})
        report_sections.append("## 3. Code Quality (Ruff)")
        report_sections.append(f"```\n{lint_res}\n```\n")
        
        # 4. Repomix による集約
        print("📦 Aggregating Context (Repomix)...")
        # repomix がインストールされている前提で run_command を使用
        repomix_cmd = "npx -y repomix --output .tmp/repomix-output.md --exclude '.git/**,.brwn/**,docs/analysis/**,logs/**'"
        repomix_res = await safe_call_tool(workspace, "run_command", {"command": repomix_cmd})
        report_sections.append("## 4. Context Aggregation (Repomix Status)")
        report_sections.append(f"```\n{repomix_res}\n```\n")
        if "ExitStatus: 0" in repomix_res:
            report_sections.append("Repository context has been packed into `.tmp/repomix-output.md`.\n")
        
        # 5. Knowledge Server による依存関係分析 (もし起動可能なら)
        print("🧠 Analyzing Knowledge Graph...")
        try:
            knowledge = await manager.start_knowledge_server(project_root, "~/.local/share/brownie/vector_db", "brownie")
            # tool 名は knowledge_server.py の実装に依存（今回は discovery の意味で list_tools 的な挙動を期待）
            kn_res = await safe_call_tool(knowledge, "get_graph_stats", {}) # 実装されていると仮定
            report_sections.append("## 5. Architectural Stats (Knowledge Server)")
            report_sections.append(f"```\n{kn_res}\n```\n")
        except Exception as e:
            report_sections.append(f"## 5. Architectural Stats\nKnowledge server failed to start: {e}\n")

    # レポート保存
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_sections))
    
    print(f"✅ Analysis report generated at: {report_path}")
    return report_path

if __name__ == "__main__":
    asyncio.run(analyze())
