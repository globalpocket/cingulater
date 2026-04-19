import asyncio
import os
import subprocess
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

    print(f"Starting process: {cmd}")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        print("Connecting client...")
        client = Client(proc)
        async with client:
            print("Connected! Listing tools...")
            tools = await client.list_tools()
            print(f"Tools: {tools}")
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        proc.terminate()
        proc.wait()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
