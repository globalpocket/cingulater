import asyncio
import os
import sys

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport


async def main():
    repo_path = os.getcwd()
    memory_path = "/tmp/brownie_mem"
    repo_name = "test-repo"
    
    command = sys.executable
    args = ["-m", "src.mcp_server.knowledge_server", repo_path, memory_path, repo_name]
    env = {**os.environ, "PYTHONPATH": "."}
    
    print(f"Connecting client with StdioTransport: {command} {args}")
    try:
        # 明示的に StdioTransport を使用する
        transport = StdioTransport(command=command, args=args, env=env)
        client = Client(transport)
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
