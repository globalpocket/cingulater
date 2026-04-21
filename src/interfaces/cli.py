import requests
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown

console = Console()


def chat_loop(api_url: str = "http://localhost:8137/v1"):
    """Brownie エンジンと直接壁打ちする対話ループ"""
    console.print("[bold cyan]BROWNIE Interactive CLI (OpenAI Protocol)[/bold cyan]")
    console.print(f"Connected to: {api_url}")
    console.print("Type 'exit' or 'quit' to stop.")
    console.print(
        "Press [bold yellow]Enter[/bold yellow] to submit, "
        "[bold yellow]Alt+Enter[/bold yellow] for newline.\n"
    )

    history = []

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    session = PromptSession(key_bindings=kb)

    while True:
        try:
            # prompt_toolkit を使用して日本語入力とマルチラインをサポート
            user_input = session.prompt(
                HTML('<style fg="ansigreen" font="bold">You &gt; </style>'),
                multiline=True,
            )

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit"]:
                break

            history.append({"role": "user", "content": user_input})

            with console.status(
                "[bold yellow]Brownie is thinking "
                "(Autonomous reasoning in progress)...[/bold yellow]"
            ):
                response = requests.post(
                    f"{api_url}/chat/completions",
                    json={"model": "brownie-v2", "messages": history, "stream": False},
                    timeout=300,  # 自律実行のためにタイムアウトを長く設定
                )

            if response.status_code == 200:
                result = response.json()
                assistant_message = result["choices"][0]["message"]["content"]

                console.print("\n[bold cyan]Brownie >[/bold cyan]")
                console.print(Markdown(assistant_message))
                console.print("-" * 40)

                history.append({"role": "assistant", "content": assistant_message})
            else:
                console.print(
                    f"[bold red]Error:[/bold red] API returned {response.status_code}"
                )
                console.print(response.text)

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[bold red]Connection Error:[/bold red] {e}")
            break


if __name__ == "__main__":
    typer.run(chat_loop)
