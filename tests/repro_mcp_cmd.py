import asyncio
import os
import sys

from fastmcp import Client


async def main():
    repo_path = os.getcwd()
    memory_path = "/tmp/brownie_mem"
    repo_name = "test-repo"

    cmd = [
        sys.executable,
        "-m",
        "src.mcp.knowledge_server",
        repo_path,
        memory_path,
        repo_name,
    ]
    env = {**os.environ, "PYTHONPATH": "."}

    print(f"Connecting client with cmd: {cmd}")
    try:
        # Popen オブジェクトではなくコマンドリストを直接渡す
        client = Client(cmd, env=env)
        async with client:
            print("Connected! Listing tools...")
            tools = await client.list_tools()
            print(f"Tools count: {len(tools)}")
            for t in tools:
                print(f" - {t.name}")
    except Exception as e:
        print(f"Failed: {e}")
        import traceback

        traceback.print_exc()
    finally:
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
