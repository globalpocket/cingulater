from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import os
import traceback

logger = setup_logging(__name__)
mcp = create_mcp_server("trace_analyzer")

@mcp.tool()
@mcp_tool_errorhandler
async def analyze_stack_trace(trace_text: str) -> str:
    """提供されたスタックトレースやログテキストをパースし、エラーの根本原因や関連ファイルを構造化して返します。"""
    try:
        # 簡単なヒューリスティックによる解析
        lines = trace_text.strip().split("\n")
        files_involved = []
        error_type = "UnknownError"
        
        for line in lines:
            line = line.strip()
            if line.startswith("File "):
                parts = line.split('"')
                if len(parts) >= 3:
                    file_path = parts[1]
                    files_involved.append(file_path)
            elif "Error:" in line or "Exception:" in line:
                error_type = line
                
        # 一意にする
        files_involved = list(dict.fromkeys(files_involved))
        
        report = [
            f"Trace Analysis Report",
            f"Detected Error: {error_type}",
            f"Files involved in trace:"
        ]
        
        for f in files_involved:
            report.append(f" - {f}")
            
        return "\n".join(report)
    except Exception as e:
        return f"Analysis failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
