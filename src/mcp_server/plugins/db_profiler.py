from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import sqlite3
import os

logger = setup_logging(__name__)
mcp = create_mcp_server("db_profiler")

@mcp.tool()
@mcp_tool_errorhandler
async def profile_database_schema(db_path: str) -> str:
    """指定されたSQLiteデータベースファイルのスキーマや構成を分析し、最適化の提案（インデックス欠落等）を行います。"""
    if not os.path.exists(db_path):
        return f"Error: Database file not found {db_path}"
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        report = ["Database Schema Profile:"]
        for table in tables:
            report.append(f"\nTable: {table}")
            
            cursor.execute(f"PRAGMA table_info({table});")
            columns = cursor.fetchall()
            for col in columns:
                report.append(f"  - {col[1]} ({col[2]})")
                
            cursor.execute(f"PRAGMA index_list({table});")
            indices = cursor.fetchall()
            if indices:
                report.append("  Indices:")
                for idx in indices:
                    report.append(f"    - {idx[1]}")
            else:
                report.append("  Indices: None (Warning: Possible N+1 or slow query issues)")
                
        conn.close()
        return "\n".join(report)
    except Exception as e:
        return f"Profiling failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
