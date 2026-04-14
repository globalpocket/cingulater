from fastmcp import FastMCP
import os
import logging
import duckdb
import networkx as nx
import asyncio
import hashlib
from typing import List, Dict, Any, Optional

try:
    from tree_sitter import Language, Parser, Query, QueryCursor
    import tree_sitter_python
    import tree_sitter_javascript
    import tree_sitter_typescript
    import tree_sitter_go
except ImportError:
    pass

# Logger settings
logger = logging.getLogger(__name__)

mcp = FastMCP("code_static_analyzer")

class AnalyzerLogic:
    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.brwn_dir = os.path.join(self.repo_root, ".brwn")
        self.db_path = os.path.join(self.brwn_dir, "index.db")
        os.makedirs(self.brwn_dir, exist_ok=True)
        self.conn = duckdb.connect(self.db_path)
        self._init_db()
        self.parsers = self._init_parsers()

    def _init_db(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, hash TEXT, last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS symbols_id_seq")
        self.conn.execute("CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY DEFAULT nextval('symbols_id_seq'), file_path TEXT, name TEXT, type TEXT, start_line INTEGER, end_line INTEGER)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS calls (caller_name TEXT, callee_name TEXT, file_path TEXT, line INTEGER)")

    def _init_parsers(self):
        parsers = {}
        try:
            parsers['python'] = Parser(Language(tree_sitter_python.language()))
            parsers['javascript'] = Parser(Language(tree_sitter_javascript.language()))
            parsers['typescript'] = Parser(Language(tree_sitter_typescript.language_typescript()))
            parsers['go'] = Parser(Language(tree_sitter_go.language()))
        except Exception as e:
            logger.error(f"Parsers init failed: {e}")
        return parsers

    def _get_queries(self, lang: str):
        if lang == 'python':
            return "(class_definition name: (identifier) @class.name) @class.def (function_definition name: (identifier) @func.name) @func.def (call function: (identifier) @call.name) @call.expr (call function: (attribute attribute: (identifier) @call.name)) @call.expr"
        return ""

    def scan_file(self, full_path: str, rel_path: str):
        # Implementation similar to original core.py
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            ext = os.path.splitext(full_path)[1].lower()
            lang_key = 'python' if ext == '.py' else 'javascript' if ext in ('.js', '.ts') else 'go'
            parser = self.parsers.get(lang_key)
            if not parser: return

            tree = parser.parse(bytes(content, "utf-8"))
            query_str = self._get_queries(lang_key)
            if not query_str: return

            query = Query(parser.language, query_str)
            cursor = QueryCursor(query)
            captures = cursor.captures(tree.root_node)

            self.conn.execute("DELETE FROM symbols WHERE file_path = ?", (rel_path,))
            self.conn.execute("DELETE FROM calls WHERE file_path = ?", (rel_path,))

            for node, tag in captures:
                start_line = node.start_point[0] + 1
                if tag.endswith(".name"):
                    name = node.text.decode('utf-8')
                    symbol_type = tag.split('.')[0]
                    self.conn.execute("INSERT INTO symbols (file_path, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)", (rel_path, name, symbol_type, start_line, node.end_point[0] + 1))
                elif tag == "call.name":
                    self.conn.execute("INSERT INTO calls (caller_name, callee_name, file_path, line) VALUES (?, ?, ?, ?)", ("global", node.text.decode('utf-8'), rel_path, start_line))
        except Exception as e:
            logger.error(f"Scan error {rel_path}: {e}")

@mcp.tool()
async def deep_scan_project(repo_path: str) -> str:
    """リポジトリ全体をスキャンし、シンボルと呼び出し関係をDBにインデックスします。"""
    logic = AnalyzerLogic(repo_path)
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(('.py', '.js', '.ts', '.go')):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, repo_path)
                logic.scan_file(full_path, rel_path)
    return f"Scan completed for {repo_path}"

@mcp.tool()
async def get_critical_path(repo_path: str, top_k: int = 5) -> str:
    """NetworkXを使用してプロジェクトの重要ノード（影響範囲の広いシンボル）を特定します。"""
    logic = AnalyzerLogic(repo_path)
    graph = nx.DiGraph()
    calls = logic.conn.execute("SELECT caller_name, callee_name, file_path, line FROM calls").fetchall()
    for caller, callee, file, line in calls:
        graph.add_edge(caller, callee, file=file, line=line)
    
    if not graph.nodes:
        return "No call graph data found. Run deep_scan_project first."
        
    out_degrees = dict(graph.out_degree())
    sorted_nodes = sorted(out_degrees.items(), key=lambda x: x[1], reverse=True)
    
    result = ["Critical symbols (by Out-Degree):"]
    for symbol, degree in sorted_nodes[:top_k]:
        result.append(f"- {symbol}: {degree} connections")
    return "\n".join(result)

@mcp.tool()
async def trace_call_flow(repo_path: str, entry_symbol: str) -> str:
    """指定されたシンボルからの呼び出しフローを追跡し、Mermaid形式で出力します。"""
    logic = AnalyzerLogic(repo_path)
    graph = nx.DiGraph()
    calls = logic.conn.execute("SELECT caller_name, callee_name, file_path, line FROM calls").fetchall()
    for caller, callee, file, line in calls:
        graph.add_edge(caller, callee, file=file, line=line)

    if entry_symbol not in graph:
        return f"Symbol '{entry_symbol}' not found in call graph."

    lines = ["sequenceDiagram", "    autonumber"]
    visited = set()
    queue = [(entry_symbol, 0)]
    while queue:
        curr, depth = queue.pop(0)
        if depth > 3: continue
        for neighbor in graph.successors(curr):
            data = graph.get_edge_data(curr, neighbor)
            lines.append(f"    {curr}->>+ {neighbor}: call ({data['file']}:{data['line']})")
            lines.append(f"    {neighbor}-->>- {curr}: return")
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth+1))
    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run(transport="stdio")
