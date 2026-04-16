import asyncio
import os
import sys

from fastmcp import Client


async def main():
    repo_path = os.getcwd()
    memory_path = "/tmp/brownie_mem"
    repo_name = "test-repo"
    
    # コマンドを文字列として結合（またはリストで試す）
    cmd_str = f"{sys.executable} -m src.mcp_server.knowledge_server {repo_path} {memory_path} {repo_name}"
    env = {**os.environ, "PYTHONPATH": "."}
    
    # 環境変数を反映させるために sh -c や env コマンドを使う必要があるかもしれないが、
    # ここではシンプルに Client に渡す方法を探る
    
    print(f"Connecting client with cmd_str: {cmd_str}")
    try:
        # FastMCP Client は文字列を受け取ると stdio サーバーとして起動しようとする
        client = Client(cmd_str)
        async with client:
            print("Connected! Listing tools...")
            tools = await client.list_tools()
            print(f"Tools count: {len(tools)}")
    except Exception as e:
        print(f"Failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
