import sys
import requests
import json
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live

console = Console()

def chat_loop(api_url: str = "http://localhost:8000/v1"):
    """Brownie エンジンと直接壁打ちする対話ループ"""
    console.print("[bold cyan]BROWNIE Interactive CLI (OpenAI Protocol)[/bold cyan]")
    console.print(f"Connected to: {api_url}")
    console.print("Type 'exit' or 'quit' to stop.\n")

    history = []

    while True:
        try:
            user_input = console.input("[bold green]You > [/bold green]")
            if user_input.lower() in ["exit", "quit"]:
                break
            
            history.append({"role": "user", "content": user_input})
            
            with console.status("[bold yellow]Brownie is thinking (Autonomous reasoning in progress)...[/bold yellow]"):
                response = requests.post(
                    f"{api_url}/chat/completions",
                    json={
                        "model": "brownie-v2",
                        "messages": history,
                        "stream": False
                    },
                    timeout=300 # 自律実行のためにタイムアウトを長く設定
                )
            
            if response.status_code == 200:
                result = response.json()
                assistant_message = result["choices"][0]["message"]["content"]
                
                console.print("\n[bold cyan]Brownie >[/bold cyan]")
                console.print(Markdown(assistant_message))
                console.print("-" * 40)
                
                history.append({"role": "assistant", "content": assistant_message})
            else:
                console.print(f"[bold red]Error:[/bold red] API returned {response.status_code}")
                console.print(response.text)
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[bold red]Connection Error:[/bold red] {e}")
            break

if __name__ == "__main__":
    typer.run(chat_loop)
