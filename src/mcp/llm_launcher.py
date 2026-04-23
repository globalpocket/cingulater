import os
import signal
import socket
import subprocess
from mcp.server.fastmcp import FastMCP

# LLM Launcher MCP Server の定義
mcp = FastMCP("LLM Launcher")


@mcp.tool()
def check_llm_status(port: int) -> bool:
    """指定されたポートでLLMサーバーが稼働しているか確認する"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


@mcp.tool()
def launch_llm_server(model_name: str, port: int) -> str:
    """mlx_lm.server をバックグラウンドプロセスとして起動する"""
    if check_llm_status(port):
        return f"Port {port} is already in use."

    base_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    venv_python = os.path.join(base_dir, ".venv", "bin", "python")

    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # ポート番号ごとにログファイルを分ける
    log_file_path = os.path.join(log_dir, f"mlx_{port}.log")
    log_file = open(log_file_path, "a")

    env = os.environ.copy()

    try:
        process = subprocess.Popen(
            [
                venv_python,
                "-m",
                "mlx_lm.server",
                "--model",
                model_name,
                "--port",
                str(port),
            ],
            stdout=log_file,
            stderr=log_file,
            env=env,
            start_new_session=True,
            cwd=base_dir,
        )
        # PIDをファイルに記録しておく（後で停止するため）
        pid_file_path = os.path.join(log_dir, f"mlx_{port}.pid")
        with open(pid_file_path, "w") as f:
            f.write(str(process.pid))

        return f"Started LLM server for model {model_name} on port {port} (PID: {process.pid})"
    except Exception as e:
        return f"Failed to start LLM server: {e}"


@mcp.tool()
def shutdown_llm_server(port: int) -> str:
    """指定されたポートのLLMサーバープロセス（PIDファイルベース）を停止する"""
    base_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    pid_file_path = os.path.join(base_dir, "logs", f"mlx_{port}.pid")

    if os.path.exists(pid_file_path):
        with open(pid_file_path, "r") as f:
            try:
                pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                os.remove(pid_file_path)
                return f"Stopped LLM server on port {port} (PID: {pid})"
            except Exception as e:
                return f"Error stopping process: {e}"

    return f"No PID file found for port {port}. Cannot stop automatically."


if __name__ == "__main__":
    # MCPサーバーとしての起動エントリーポイント
    mcp.run()
