from pathlib import Path

import pytest
from src.mcp_server.manager import MCPServerManager


@pytest.mark.asyncio
async def test_mcp_jit_loading():
    """MCP サーバーの動的 JIT ロードのテスト"""
    project_root = str(Path(__file__).parent.parent)
    manager = MCPServerManager(project_root)

    async with manager:
        # コアサーバーの起動
        workspace_client = await manager.start_workspace_server(
            "/tmp", "/tmp", 1000, 1000
        )
        knowledge_client = await manager.start_knowledge_server(
            "/tmp", "/tmp/memory", "test/test"
        )

        # コアが起動していることを確認
        assert workspace_client is not None
        assert knowledge_client is not None
        assert len(manager.plugin_clients) == 0

        # JIT プロビジョニング
        await manager.provision_servers(["web_fetch", "trace_analyzer"])

        # プロビジョニングされたことを確認
        assert "web_fetch" in manager.plugin_clients
        assert "trace_analyzer" in manager.plugin_clients
        assert len(manager.plugin_clients) == 2

        # 不要なものを停止し、新しいものをプロビジョニング
        await manager.provision_servers(["web_fetch", "security_analyzer"])

        # trace_analyzer が停止し、security_analyzer が起動したことを確認
        assert "web_fetch" in manager.plugin_clients
        assert "security_analyzer" in manager.plugin_clients
        assert "trace_analyzer" not in manager.plugin_clients
        assert len(manager.plugin_clients) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
