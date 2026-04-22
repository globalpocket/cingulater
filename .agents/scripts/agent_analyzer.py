#!/usr/bin/env python3
import asyncio
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime

# 解析結果の出力先
ANALYZE_DIR = Path(".analyze")

async def run_tool(command: list, output_file: Path = None, description: str = ""):
    """コマンドを実行し、結果をファイルまたは標準出力に返す"""
    print(f"🚀 [ANALYZER] {description}...")
    try:
        env = os.environ.copy()
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()
        
        out_text = stdout.decode().strip()
        err_text = stderr.decode().strip()
        
        if output_file:
            ANALYZE_DIR.mkdir(exist_ok=True)
            # 正常終了でなくても（警告など）、出力を保存する
            content = out_text if out_text else err_text
            output_file.write_text(content)
            print(f"✅ Saved to {output_file}")
        
        return out_text
    except Exception as e:
        print(f"❌ Error running {description}: {e}")
        return str(e)

async def analyze():
    print(f"🔍 [AGENT] Starting Project Analysis at {datetime.now().isoformat()}...")
    ANALYZE_DIR.mkdir(exist_ok=True)

    # 1. Repomix (全体俯瞰)
    await run_tool(
        ["npx", "repomix", "--output", str(ANALYZE_DIR / "repomix.txt"), "--include", "src/**", "--no-copy"],
        output_file=ANALYZE_DIR / "repomix.txt",
        description="Aggregating context (Repomix)"
    )

    # 2. Ruff (Linting)
    await run_tool(
        ["ruff", "check", "src"],
        output_file=ANALYZE_DIR / "lint_report.md",
        description="Checking code hygiene (Ruff)"
    )

    # 3. Semgrep (Security Scan)
    await run_tool(
        ["semgrep", "scan", "--config", "auto", "src", "--json"],
        output_file=ANALYZE_DIR / "semgrep_results.json",
        description="Running security scan (Semgrep)"
    )

    # 4. ast-grep (Structural Analysis)
    await run_tool(
        ["sg", "scan", "--pattern", "class $CLASS: $$$", "src", "--json"],
        output_file=ANALYZE_DIR / "structure.json",
        description="Analyzing structure (ast-grep)"
    )

    # 5. Bandit (Security specific)
    await run_tool(
        ["bandit", "-r", "src", "-f", "json"],
        output_file=ANALYZE_DIR / "bandit_results.json",
        description="Running Bandit security scan"
    )

    print(f"\n✨ Analysis complete. Results are persistently stored in: {ANALYZE_DIR.absolute()}")

if __name__ == "__main__":
    asyncio.run(analyze())
