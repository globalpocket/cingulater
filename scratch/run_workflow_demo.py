import asyncio
import logging
import os
import sys
from pathlib import Path

# プロジェクトルートと src を PATH に追加
project_root = Path(__file__).parent.parent.absolute()
sys.path.append(str(project_root))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.core.workflow_manager import WorkflowLoader

# ログ設定 (デモ用なので INFO にして詳細を出す)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("brownie.demo")

console = Console()

async def run_demo():
    console.print(Panel("[bold cyan]BROWNIE Dynamic Workflow Engine Demo[/bold cyan]", expand=False))
    
    # モックモードの判定
    mock_mode = os.getenv("DEMO_MOCK", "true").lower() == "true"
    
    if mock_mode:
        console.print("[bold yellow]Running in MOCK mode (Testing LangGraph flow without real LLM)[/bold yellow]")
        from unittest.mock import patch

        from pydantic_ai.models.test import TestModel
        
        # TestModel を使用して、呼び出しごとに固定のレスポンスを返すようにする
        mock_model = TestModel()
        patcher = patch("src.core.workflow_manager.get_robust_model", return_value=mock_model)
        patcher.start()

    # 1. WorkflowLoader の初期化
    workspace_root = project_root
    loader = WorkflowLoader(project_root, workspace_root)
    
    # 2. Config の設定
    config = {
        "llm": {
            "models": {
                "planner": "mock-planner",
                "executor": "mock-executor"
            },
            "planner_endpoint": "http://localhost:8080",
            "executor_endpoint": "http://localhost:8081"
        }
    }
    
    console.print("[yellow]Loading workflows...[/yellow]")
    tools = loader.load_all(config=config)
    
    if "hello_workflow" not in tools:
        console.print("[red]Error: 'hello_workflow' could not be loaded.[/red]")
        console.print(f"Available tools: {list(tools.keys())}")
        # .brwn/workflows の中身を確認
        wf_path = project_root / ".brwn" / "workflows"
        if wf_path.exists():
            console.print(f"Files in {wf_path}: {[f.name for f in wf_path.iterdir()]}")
        return

    hello_workflow = tools["hello_workflow"]
    
    # 3. ワークフローの実行
    input_data = "こんにちは、BROWNIE！新機能のデモを開始してください。"
    console.print(Panel(f"[bold]Input Data:[/bold] {input_data}", title="Workflow Execution Request", border_style="cyan"))
    
    console.print("[green]Invoking LangGraph...[/green]")
    
    try:
        # 実行
        result_state = await hello_workflow(input_data=input_data)
        
        # 4. 結果の表示
        console.print("\n[bold green]Execution Results[/bold green]")
        
        # 結果テーブル
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Node ID", style="cyan")
        table.add_column("Output", style="white")
        
        results = result_state.get("results", {})
        for node_id, output in results.items():
            table.add_row(node_id, str(output))
        
        console.print(table)
        
        # 最終出力を強調
        final_summary = list(results.values())[-1] if results else "No output"
        console.print(Panel(f"{final_summary}", title="Final Summary Output", border_style="bold yellow"))
        
        console.print("\n[bold green]Demo completed successfully![/bold green]")
        
    except Exception as e:
        console.print(f"[red]Execution failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(run_demo())
