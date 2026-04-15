import asyncio
import logging
from pathlib import Path
import os
import yaml
import sys

# プロジェクトルートを PATH に追加
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.core.workflow_manager import WorkflowLoader

logging.basicConfig(level=logging.INFO)

async def test_manual():
    # 仮のディレクトリ構造作成
    tmp_path = Path("/tmp/brownie_test")
    if tmp_path.exists():
        import shutil
        shutil.rmtree(tmp_path)
    tmp_path.mkdir(parents=True)
    
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    
    wf_dir = project_dir / "workflows"
    wf_dir.mkdir()
    
    # YAML ツール作成
    wf_yaml = wf_dir / "greet_flow.yaml"
    wf_yaml.write_text("""
description: "Greeting workflow"
start_node: "hello"
nodes:
  hello:
    prompt_file: "hello.md"
    next: "END"
""", encoding="utf-8")
    
    # MD ツール作成
    hello_md = wf_dir / "hello.md"
    hello_md.write_text("Hello from MD", encoding="utf-8")
    
    print(f"--- Loading tools from {project_dir} ---")
    loader = WorkflowLoader(project_dir)
    
    # ダミー config
    config = {
        "llm": {
            "models": {"planner": "mock-p", "executor": "mock-e"},
            "planner_endpoint": "http://localhost:8080",
            "executor_endpoint": "http://localhost:8080"
        }
    }
    
    tools = loader.load_all(config=config)
    
    if "greet_flow" in tools:
        print("SUCCESS: Greet flow loaded.")
        tool = tools["greet_flow"]
        print(f"Tool Name: {tool.__name__}")
        print(f"Tool Doc: {tool.__doc__}")
    else:
        print("FAILED: Greet flow not loaded.")
        sys.exit(1)

    if "hello" in tools:
        print("SUCCESS: Hello prompt loaded.")
    else:
        print("FAILED: Hello prompt not loaded.")
        sys.exit(1)

    print("--- Test Completed Successfully ---")

if __name__ == "__main__":
    asyncio.run(test_manual())
