import os

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("dep_audit")

@mcp.tool()
@mcp_tool_errorhandler
async def audit_dependencies() -> str:
    """uv.lock や pyproject.toml を解析してパッケージ競合や脆弱性の懸念点を抽出します。"""
    # 簡易モック: 実際には uv や pip-audit をラップする。
    try:
        if os.path.exists("uv.lock"):
            with open("uv.lock", "r") as f:
                content = f.read()
            # 簡易な解析例（パースはせず、サイズ等をレポートするだけ）
            num_packages = content.count("[[package]]")
            return f"Found {num_packages} packages in uv.lock.\n(In a real implementation, this would check against a vulnerability DB via pip-audit etc.)"
        else:
            return "No uv.lock found in the current directory."
    except Exception as e:
        return f"Audit failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
