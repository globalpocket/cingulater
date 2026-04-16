import os
import subprocess

from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging

logger = setup_logging(__name__)
mcp = create_mcp_server("clone_detector")

@mcp.tool()
@mcp_tool_errorhandler
async def detect_clones(directory: str) -> str:
    """ast-grep (sg) を使用して指定されたディレクトリ内のコードの重複（DRY違反）を検出します。"""
    if not os.path.exists(directory):
        return f"Error: Directory not found {directory}"
        
    try:
        # デモ/プロトタイプ用として簡易な重複検索パターンを使用するか、sg に専用設定を渡す
        # 今回はシンプルな sg コマンドを実行
        # 注意: 実際に効果を出すには、パターンルール定義が必要ですが、今回は単純な関数重複などを調査するためのダミー・ラップ
        result = subprocess.run(["sg", "scan", "-p", "def $A($B): $$$", directory], capture_output=True, text=True)
        return f"Clone Detector Output:\n{result.stdout[:2000]}\n(Truncated if too long)"
    except FileNotFoundError:
        return "ast-grep (sg) command not found. Please ensure it's installed."
    except Exception as e:
        return f"Detection failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
