import ast
import json
import os
from collections import Counter
from typing import Dict

import networkx as nx
import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from loguru import logger
from tree_sitter import Language, Parser


class FlowTracer:
    """AST 解析とコード構造分析（Flow）を管理するクラス。"""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.graph = nx.DiGraph()
        self.parsers = self._init_parsers()

    def _init_parsers(self) -> Dict[str, Parser]:
        parsers = {}
        try:
            py_lang = Language(tree_sitter_python.language())
            parsers[".py"] = Parser(py_lang)
            js_lang = Language(tree_sitter_javascript.language())
            parsers[".js"] = Parser(js_lang)
            parsers[".jsx"] = Parser(js_lang)
            ts_lang = Language(tree_sitter_typescript.language_typescript())
            parsers[".ts"] = Parser(ts_lang)
            parsers[".tsx"] = Parser(ts_lang)
            go_lang = Language(tree_sitter_go.language())
            parsers[".go"] = Parser(go_lang)
        except Exception as e:
            logger.warning(f"Tree-sitter パーサーの初期化に失敗しました: {e}")
        return parsers

    def scan_file(self, file_path: str, content: str):
        if not file_path.endswith(".py"):
            return

        try:
            tree = ast.parse(content)
            self.graph.add_node(file_path, type="file")

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._add_symbol(
                        node.name,
                        file_path,
                        "func",
                        node.lineno,
                        getattr(node, "end_lineno", node.lineno),
                    )
                elif isinstance(node, ast.ClassDef):
                    self._add_symbol(
                        node.name,
                        file_path,
                        "class",
                        node.lineno,
                        getattr(node, "end_lineno", node.lineno),
                    )
                elif isinstance(node, ast.Import):
                    for name in node.names:
                        self.graph.add_edge(file_path, name.name, type="import")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.graph.add_edge(file_path, node.module, type="import")
        except Exception as e:
            logger.error(f"Error scanning file {file_path}: {e}")

    def refresh_graph(self):
        logger.info(f"Refreshing knowledge graph: {self.repo_path}")
        self.graph.clear()
        src_path = os.path.join(self.repo_path, "src")
        if not os.path.exists(src_path):
            return

        for root, _, files in os.walk(src_path):
            for file in files:
                if file.endswith(".py"):
                    fpath = os.path.join(root, file)
                    rel_path = os.path.relpath(fpath, self.repo_path)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            self.scan_file(rel_path, f.read())
                    except Exception as e:
                        logger.warning(f"Failed to read {fpath}: {e}")

    def _add_symbol(self, name: str, file_path: str, stype: str, start: int, end: int):
        symbol_id = f"{file_path}:{name}"
        self.graph.add_node(
            symbol_id,
            name=name,
            file_path=file_path,
            type=stype,
            start_line=start,
            end_line=end,
        )
        self.graph.add_edge(file_path, symbol_id, type="contains")

    def trace_flow(self, entry_symbol: str, depth: int = 5) -> str:
        relevant_nodes = []
        for n, d in self.graph.nodes(data=True):
            if d.get("name") == entry_symbol:
                relevant_nodes.append(n)

        if not relevant_nodes:
            return "Participant Not Found"

        lines = ["sequenceDiagram"]
        visited = set()

        def walk(node, current_depth):
            if current_depth > depth or node in visited:
                return
            visited.add(node)

            for successor in self.graph.successors(node):
                u_name = self.graph.nodes[node].get("name", node)
                v_name = self.graph.nodes[successor].get("name", successor)
                lines.append(f"    {u_name}->>+{v_name}: calls")
                walk(successor, current_depth + 1)

        for root in relevant_nodes:
            walk(root, 0)

        return "\n".join(lines)


class KnowledgeBaseProvider:
    """リポジトリの知識管理と分析を行うコアプロバイダー。"""

    def __init__(self, repo_path: str, repo_name: str):
        self.repo_path = repo_path
        self.repo_name = repo_name
        self.tracer = FlowTracer(repo_path)
        self.tracer.refresh_graph()

    def get_summary(self) -> dict:
        """リポジトリの全体像を構築します。"""
        return {
            "repo_name": self.repo_name,
            "repo_path": self.repo_path,
            "tech_stack": self._detect_tech_stack(),
            "statistics": self._query_statistics(),
            "top_classes": self._query_top_symbols("class", 20),
            "top_functions": self._query_top_symbols("func", 20),
            "hotspots": self._detect_hotspots(),
            "entry_points": self._detect_entry_points(),
        }

    def _detect_tech_stack(self) -> dict:
        stack = {"languages": [], "frameworks": [], "build_tools": []}
        pyproject = os.path.join(self.repo_path, "pyproject.toml")
        if os.path.exists(pyproject):
            stack["languages"].append("Python")
            stack["build_tools"].append("pyproject.toml")
            try:
                with open(pyproject, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                fw_markers = {
                    "fastapi": "FastAPI",
                    "django": "Django",
                    "flask": "Flask",
                    "fastmcp": "FastMCP/MCP",
                    "langchain": "LangChain",
                    "chromadb": "ChromaDB",
                }
                for marker, name in fw_markers.items():
                    if marker in content:
                        stack["frameworks"].append(name)
            except Exception as e:
                logger.warning(f"Failed to parse pyproject.toml: {e}")

        pkg_json = os.path.join(self.repo_path, "package.json")
        if os.path.exists(pkg_json):
            stack["languages"].append("JavaScript/TypeScript")
            stack["build_tools"].append("package.json")
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                }
                js_fw = {"react": "React", "vue": "Vue", "next": "Next.js"}
                for marker, name in js_fw.items():
                    if marker in deps:
                        stack["frameworks"].append(name)
            except Exception:
                pass

        return stack

    def _query_statistics(self) -> dict:
        try:
            nodes = self.tracer.graph.nodes(data=True)
            files_count = len([n for n, d in nodes if d.get("type") == "file"])
            symbols_count = len(
                [n for n, d in nodes if d.get("type") in ("func", "class")]
            )
            return {"files": files_count, "symbols": symbols_count}
        except Exception as e:
            return {"error": str(e)}

    def _query_top_symbols(self, symbol_type: str, limit: int) -> list:
        try:
            nodes = [
                (n, d)
                for n, d in self.tracer.graph.nodes(data=True)
                if d.get("type") == symbol_type
            ]
            sorted_nodes = sorted(
                nodes, key=lambda x: self.tracer.graph.in_degree(x[0]), reverse=True
            )
            return [
                {
                    "name": d["name"],
                    "file": d["file_path"],
                    "references": self.tracer.graph.in_degree(n),
                }
                for n, d in sorted_nodes[:limit]
            ]
        except Exception:
            return []

    def _detect_hotspots(self) -> list:
        try:
            nodes = [
                d.get("file_path", "")
                for n, d in self.tracer.graph.nodes(data=True)
                if d.get("type") == "file"
            ]
            dirs = [os.path.dirname(p) or "." for p in nodes]
            counts = Counter(dirs).most_common(10)
            return [{"directory": d, "file_count": c} for d, c in counts]
        except Exception:
            return []

    def _detect_entry_points(self) -> list:
        try:
            entry_names = ["main", "__main__", "start", "run", "app"]
            entries = []
            for n, d in self.tracer.graph.nodes(data=True):
                if d.get("name") in entry_names:
                    deps = list(self.tracer.graph.successors(n))
                    entries.append(
                        {
                            "name": d["name"],
                            "file": d["file_path"],
                            "dependencies": deps[:5],
                        }
                    )
            return entries
        except Exception:
            return []
