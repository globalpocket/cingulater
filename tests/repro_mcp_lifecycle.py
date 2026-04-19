import asyncio
import os
import sys

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport


async def main():
    repo_path = os.getcwd()
    memory_path = "/tmp/brownie_mem"
    repo_name = "test-repo"

    command = (
        f"{sys.executable} -m src.mcp_server.knowledge_server "
        f"{repo_path} {memory_path} {repo_name}"
    )
    args = []
    env = {**os.environ, "PYTHONPATH": "."}

    print("Connecting client...")
    transport = StdioTransport(command=command, args=args, env=env)
    client = Client(transport)

    async with client:
        print("Connected!")
        # プロセスが生きているか確認
        # StdioTransport の内部構造を探るのは控えたいが、接続は成功している
        pass

    print("Disconnected.")
    # プロセスが終了していることを確認したいが、
    # StdioTransport(keep_alive=True) だと生き残るかも？
    # デフォルトは keep_alive=True なので、明示的に False にするか、close() を呼ぶ。

    print("Checking for remaining processes...")
    # ...
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
