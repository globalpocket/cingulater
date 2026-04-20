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
        """ファイルを解析してシンボル情報をグラフに登録します。"""
        ext = os.path.splitext(file_path)[1]
        parser = self.parsers.get(ext)
        if not parser:
            return

        try:
            tree = parser.parse(bytes(content, "utf8"))
            self.graph.add_node(file_path, type="file")

            # 言語ごとのシンボル抽出クエリ
            queries = {
                ".py": (
                    "(function_definition name: (identifier) @name) "
                    "(class_definition name: (identifier) @name)"
                ),
                ".js": (
                    "(function_declaration name: (identifier) @name) "
                    "(class_declaration name: (identifier) @name) "
                    "(variable_declarator name: (identifier) @name "
                    "value: (arrow_function))"
                ),
                ".ts": (
                    "(function_declaration name: (identifier) @name) "
                    "(class_declaration name: (identifier) @name) "
                    "(interface_declaration name: (identifier) @name)"
                ),
                ".tsx": (
                    "(function_declaration name: (identifier) @name) "
                    "(class_declaration name: (identifier) @name)"
                ),
                ".go": (
                    "(function_declaration name: (identifier) @name) "
                    "(type_spec name: (identifier) @name)"
                ),
            }

            query_str = queries.get(ext)
            if query_str:
                query = parser.language.query(query_str)
                captures = query.captures(tree.root_node)

                for node, tag in captures:
                    self._add_symbol(
                        node.text.decode("utf8"),
                        file_path,
                        "func" if tag == "name" else "class",  # 簡易判定
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                    )

            # TODO: インポート依存関係の抽出 (tree-sitter クエリによる一般化)

        except Exception as e:
            logger.error(f"Error scanning file {file_path}: {e}")

    def refresh_graph(self):
        """プロジェクト全体を走査してグラフを再構築します。"""
        logger.info(f"Refreshing knowledge graph: {self.repo_path}")
        self.graph.clear()
        src_path = os.path.join(self.repo_path, "src")
        # src がない場合はルートから
        target_root = (
            src_path if os.path.exists(src_path) else self.repo_path
        )

        valid_exts = {".py", ".js", ".jsx", ".ts", ".tsx", ".go"}

        for root, _, files in os.walk(target_root):
            if any(
                p in root
                for p in [".git", "node_modules", "vendor", "__pycache__"]
            ):
                continue
            for file in files:
                ext = os.path.splitext(file)[1]
                if ext in valid_exts:
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
