import os
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
    # 1. 環境変数の最適化 (wcwidth の文字幅計算を正確にするため)
    lang = os.environ.get("LANG", "")
    if not lang or "UTF-8" not in lang.upper():
        os.environ["LANG"] = "ja_JP.UTF-8"
    if not os.environ.get("LC_ALL"):
        os.environ["LC_ALL"] = "ja_JP.UTF-8"

    console.print("[bold cyan]🤖 Brownie Interaction Mode[/bold cyan]")

    # 2. VSCode環境の検知と警告メッセージの表示
    if os.environ.get("TERM_PROGRAM") == "vscode":
        console.print(
            "[bold red]Warning:[/bold red] VSCodeのターミナルで日本語入力時に表示が崩れる場合は、"
            "VSCodeの設定で `Terminal > Integrated: Unicode Version` を `6` に変更してください。"
        )

    console.print(f"Connected to Engine: {api_url}")
    console.print("Type 'exit' or 'quit' to end session.")
    console.print(
        "Press [bold yellow]Enter[/bold yellow] to send, "
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
                HTML('<style fg="ansigreen" font="bold">You > </style>'),
                multiline=True,
            )

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit"]:
                break

            history.append({"role": "user", "content": user_input})

            with console.status(
                "[bold yellow]Brownie is processing your request...[/bold yellow]"
            ):
                response = requests.post(
                    f"{api_url}/chat/completions",
                    json={"model": "brownie-v2", "messages": history, "stream": False},
                    timeout=300,
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
