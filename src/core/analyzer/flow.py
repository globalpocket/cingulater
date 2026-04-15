import os
import logging
import duckdb
from typing import List, Dict, Any, Optional
from tree_sitter import Language, Parser
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript
import tree_sitter_go

logger = logging.getLogger(__name__)

class FlowTracer:
    """
    AST 解析とコード構造分析（Flow）を管理するクラス。
    DuckDB をバックエンドとして使用し、シンボルや関数呼び出しの情報を永続化・クエリする。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = duckdb.connect(db_path)
        self._initialize_schema()
        self.parsers = self._init_parsers()

    def _initialize_schema(self):
        """データベーススキーマの初期化"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                last_scanned TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                name TEXT,
                file_path TEXT,
                type TEXT, -- 'class', 'func', 'method'
                start_line INTEGER,
                end_line INTEGER
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                caller_name TEXT,
                callee_name TEXT,
                file_path TEXT,
                line INTEGER
            )
        """)
        # ID 生成用のシーケンス（DuckDB）
        try:
            self.conn.execute("CREATE SEQUENCE sym_id_seq")
        except:
            pass # すでに存在する場合

    def _init_parsers(self) -> Dict[str, Parser]:
        """各種言語の Tree-sitter パーサーを初期化"""
        parsers = {}
        try:
            # Python
            py_lang = Language(tree_sitter_python.language())
            py_parser = Parser(py_lang)
            parsers[".py"] = py_parser

            # JS/TS
            js_lang = Language(tree_sitter_javascript.language())
            parsers[".js"] = Parser(js_lang)
            parsers[".jsx"] = Parser(js_lang)

            ts_lang = Language(tree_sitter_typescript.language_typescript())
            parsers[".ts"] = Parser(ts_lang)
            parsers[".tsx"] = Parser(ts_lang)

            # Go
            go_lang = Language(tree_sitter_go.language())
            parsers[".go"] = Parser(go_lang)
        except Exception as e:
            logger.warning(f"Tree-sitter パーサーの初期化に失敗しました: {e}")
        
        return parsers

    def scan_file(self, file_path: str, content: str):
        """ファイルを解析して情報を DuckDB に保存"""
        ext = os.path.splitext(file_path)[1]
        parser = self.parsers.get(ext)
        if not parser:
            return

        try:
            tree = parser.parse(bytes(content, "utf-8"))
            # 1. 既存データのクリーンアップ
            self.conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
            self.conn.execute("DELETE FROM calls WHERE file_path = ?", (file_path,))

            # 2. AST トラバースしてシンボルと呼び出しを抽出
            self._walk_tree(tree.root_node, file_path, content)
            
            # 3. ファイル情報の更新
            self.conn.execute("INSERT OR REPLACE INTO files (path, last_scanned) VALUES (?, CURRENT_TIMESTAMP)", (file_path,))
        except Exception as e:
            logger.error(f"ファイル解析エラー ({file_path}): {e}")

    def _walk_tree(self, node, file_path: str, content: str):
        """AST を再帰的にトラバースしてシンボル情報を取得（簡易実装）"""
        # 言語ごとにノードタイプが異なるが、一般的な名前でマッチングを試みる
        # 本来は言語固有のクエリファイルを使用するのがベストだが、ここでは汎用的なロジックとする
        
        node_type = node.type
        
        # シンボルの抽出 (Python の例)
        if node_type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                self._add_symbol(name_node.text.decode("utf-8"), file_path, "class", node.start_point[0], node.end_point[0])
        elif node_type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                self._add_symbol(name_node.text.decode("utf-8"), file_path, "func", node.start_point[0], node.end_point[0])
        elif node_type == "call":
            # 呼び出し情報の抽出
            func_node = node.child_by_field_name("function")
            if func_node:
                # 簡易的に最後の識別子を取得
                callee = func_node.text.decode("utf-8")
                # コンテキスト（現在がどの関数内か）は一旦省略し、ファイル単位で記録
                self.conn.execute("INSERT INTO calls (caller_name, callee_name, file_path, line) VALUES (?, ?, ?, ?)",
                                 ("global", callee, file_path, node.start_point[0]))

        # 再帰的に子ノードを処理
        for child in node.children:
            self._walk_tree(child, file_path, content)

    def _add_symbol(self, name: str, file_path: str, stype: str, start: int, end: int):
        self.conn.execute("""
            INSERT INTO symbols (id, name, file_path, type, start_line, end_line)
            VALUES (nextval('sym_id_seq'), ?, ?, ?, ?, ?)
        """, (name, file_path, stype, start, end))

    def close(self):
        self.conn.close()
