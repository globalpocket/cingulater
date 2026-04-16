"""
BROWNIE Knowledge MCP Server
=============================
「記憶（海馬）」と「構造解析（脳幹）」を MCP プロトコルで公開するサーバー。
stdio トランスポートで Orchestrator のサブプロセスとして動作する。

公開 Tool:
  - semantic_search(query, limit): ChromaDB によるセマンティック検索
  - get_code_flow(entry_symbol, depth): AST 解析による Mermaid 出力
  - get_repo_summary(): リポジトリ構造の要約

公開 Resource:
  - brownie://repo/context: WDCA コンテキスト（プロジェクト概要）
"""

from .base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import duckdb
from tree_sitter import Language, Parser
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript
import tree_sitter_go

logger = setup_logging("knowledge_server")

# --- 内部解析エンジン (FlowTracer) ---

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
            parsers[".py"] = Parser(py_lang)
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
            self.conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
            self.conn.execute("DELETE FROM calls WHERE file_path = ?", (file_path,))
            self._walk_tree(tree.root_node, file_path, content)
            self.conn.execute("INSERT OR REPLACE INTO files (path, last_scanned) VALUES (?, CURRENT_TIMESTAMP)", (file_path,))
        except Exception as e:
            logger.error(f"ファイル解析エラー ({file_path}): {e}")

    def _walk_tree(self, node, file_path: str, content: str):
        """AST を再帰的にトラバースしてシンボル情報を取得"""
        node_type = node.type
        if node_type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                self._add_symbol(name_node.text.decode("utf-8"), file_path, "class", node.start_point[0], node.end_point[0])
        elif node_type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                self._add_symbol(name_node.text.decode("utf-8"), file_path, "func", node.start_point[0], node.end_point[0])
        elif node_type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                callee = func_node.text.decode("utf-8")
                self.conn.execute("INSERT INTO calls (caller_name, callee_name, file_path, line) VALUES (?, ?, ?, ?)",
                                 ("global", callee, file_path, node.start_point[0]))
        for child in node.children:
            self._walk_tree(child, file_path, content)

    def _add_symbol(self, name: str, file_path: str, stype: str, start: int, end: int):
        self.conn.execute("""
            INSERT INTO symbols (id, name, file_path, type, start_line, end_line)
            VALUES (nextval('sym_id_seq'), ?, ?, ?, ?, ?)
        """, (name, file_path, stype, start, end))

    def close(self):
        self.conn.close()

# --- サーバーインスタンスの生成 ---
mcp = create_mcp_server("BrownieKnowledge")

# --- グローバル状態（起動時に初期化） ---
_repo_path: str = ""
_repo_name: str = ""
_memory_path: str = ""
_tracer = None
_memory = None


def _validate_path(target: str, base: str) -> str:
    """Path Traversal 防御: 対象パスがベースディレクトリ配下にあることを検証"""
    resolved = os.path.realpath(target)
    base_resolved = os.path.realpath(base)
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise ValueError(f"アクセス拒否: パス '{target}' はリポジトリ外です。")
    return resolved


def _get_tracer():
    """FlowTracer のレイジー初期化"""
    global _tracer
    if _tracer is not None:
        return _tracer

    db_path = os.path.join(_repo_path, ".brwn", "index.db")
    if not os.path.exists(db_path):
        return None
    _tracer = FlowTracer(db_path)
    return _tracer


def _get_memory():
    """HistoryServer のレイジー初期化"""
    global _memory
    if _memory is not None:
        return _memory

    from .history_server import HistoryServer
    _memory = HistoryServer()
    return _memory


# ============================================================
# MCP Tool: semantic_search
# ============================================================
@mcp.tool()
@mcp_tool_errorhandler
async def semantic_search(query: str, limit: int = 5) -> str:
    """コードベースからセマンティック検索を実行します。
    過去の実装経験や類似コードスニペットを探索できます。

    Args:
        query: 検索クエリ文字列
        limit: 返却する最大件数（デフォルト: 5）
    """
    memory = _get_memory()
    if memory is None:
        return json.dumps({"error": "HistoryServer が初期化されていません。"}, ensure_ascii=False)

    # ChromaDB の内部 I/O はブロッキングのため、スレッドで実行
    results = await asyncio.to_thread(
        _sync_search_memory, memory, query, _repo_name, limit
    )
    return json.dumps(results, ensure_ascii=False, indent=2)


def _sync_search_memory(memory, query: str, repo_name: str, limit: int):
    """HistoryServer.search_memory の同期ラッパー（to_thread 用）"""
    # search_memory は async def だが内部は同期的。直接 collection.query を呼ぶ
    results = memory.collection.query(
        query_texts=[query],
        where={"repo_name": repo_name},
        n_results=limit
    )
    memories = []
    if results['documents'] and results['documents'][0]:
        for i in range(len(results['documents'][0])):
            memories.append({
                "content": results['documents'][0][i],
                "metadata": results['metadatas'][0][i],
                "distance": results['distances'][0][i]
            })
    return memories


# ============================================================
# MCP Tool: get_code_flow
# ============================================================
@mcp.tool()
@mcp_tool_errorhandler
async def get_code_flow(entry_symbol: str, depth: int = 5) -> str:
    """シンボル名（関数名やクラス名）から始まる処理フローを追跡し、
    Mermaid sequenceDiagram 形式で返します。

    Args:
        entry_symbol: 追跡開始するシンボル名（例: "plan_and_execute"）
        depth: 追跡の最大深度（デフォルト: 5）
    """
    tracer = _get_tracer()
    if tracer is None:
        return f"解析インデックスが見つかりません。.brwn/index.db が存在するか確認してください。"

    # CPU バウンドな追跡処理をスレッドで実行（既存の非同期性を維持）
    flow_data = await asyncio.to_thread(tracer.trace_flow, entry_symbol, int(depth))
    return f"### {entry_symbol} の処理フロー\n\n```mermaid\n{flow_data}\n```"


# ============================================================
# MCP Tool: get_repo_summary
# ============================================================
@mcp.tool()
@mcp_tool_errorhandler
async def get_repo_summary() -> str:
    """リポジトリの構造サマリーを返します。
    技術スタック、ファイル数、シンボル数、主要クラス、
    ホットスポット（ファイル密度の高いディレクトリ）、
    モジュール間の結合度を含みます。
    """
    summary = await asyncio.to_thread(_build_repo_summary)
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _build_repo_summary() -> dict:
    """リポジトリ要約を構築する同期関数"""
    result = {
        "repo_name": _repo_name,
        "repo_path": _repo_path,
        "tech_stack": _detect_tech_stack(),
        "statistics": _query_statistics(),
        "top_classes": _query_top_symbols("class", 20),
        "top_functions": _query_top_symbols("func", 20),
        "hotspots": _detect_hotspots(),
        "entry_points": _detect_entry_points(),
    }
    return result


def _detect_tech_stack() -> dict:
    """pyproject.toml, package.json 等から技術スタックを判定"""
    stack = {"languages": [], "frameworks": [], "build_tools": []}

    pyproject = os.path.join(_repo_path, "pyproject.toml")
    if os.path.exists(pyproject):
        stack["languages"].append("Python")
        stack["build_tools"].append("pyproject.toml")
        try:
            with open(pyproject, "r", encoding="utf-8") as f:
                content = f.read()
            # 主要フレームワークの検出
            fw_markers = {
                "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
                "fastmcp": "FastMCP/MCP", "langchain": "LangChain",
                "chromadb": "ChromaDB", "duckdb": "DuckDB",
            }
            for marker, name in fw_markers.items():
                if marker in content.lower():
                    stack["frameworks"].append(name)
        except Exception:
            pass

    pkg_json = os.path.join(_repo_path, "package.json")
    if os.path.exists(pkg_json):
        stack["languages"].append("JavaScript/TypeScript")
        stack["build_tools"].append("package.json")
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            js_fw = {"react": "React", "vue": "Vue", "next": "Next.js", "express": "Express"}
            for marker, name in js_fw.items():
                if marker in deps:
                    stack["frameworks"].append(name)
        except Exception:
            pass

    go_mod = os.path.join(_repo_path, "go.mod")
    if os.path.exists(go_mod):
        stack["languages"].append("Go")
        stack["build_tools"].append("go.mod")

    return stack


def _query_statistics() -> dict:
    """DuckDB からファイル数・シンボル数を集計"""
    tracer = _get_tracer()
    if tracer is None:
        return {"files": 0, "symbols": 0, "calls": 0}

    try:
        files_count = tracer.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbols_count = tracer.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        calls_count = tracer.conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        return {"files": files_count, "symbols": symbols_count, "calls": calls_count}
    except Exception as e:
        logger.error(f"統計クエリ失敗: {e}")
        return {"files": 0, "symbols": 0, "calls": 0, "error": str(e)}


def _query_top_symbols(symbol_type: str, limit: int) -> list:
    """指定タイプの主要シンボルを取得"""
    tracer = _get_tracer()
    if tracer is None:
        return []

    try:
        rows = tracer.conn.execute("""
            SELECT s.name, s.file_path, COUNT(c.callee_name) as ref_count
            FROM symbols s
            LEFT JOIN calls c ON s.name = c.callee_name
            WHERE s.type = ?
            GROUP BY s.name, s.file_path
            ORDER BY ref_count DESC
            LIMIT ?
        """, (symbol_type, limit)).fetchall()
        return [{"name": r[0], "file": r[1], "references": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"シンボルクエリ失敗: {e}")
        return []


def _detect_hotspots() -> list:
    """ファイル密度の高いディレクトリ（ホットスポット）を検出"""
    tracer = _get_tracer()
    if tracer is None:
        return []

    try:
        # ディレクトリごとのファイル数を集計
        rows = tracer.conn.execute("""
            SELECT
                CASE
                    WHEN POSITION('/' IN path) > 0
                    THEN SUBSTRING(path, 1, POSITION('/' IN path) - 1)
                    ELSE '.'
                END as dir_name,
                COUNT(*) as file_count
            FROM files
            GROUP BY dir_name
            ORDER BY file_count DESC
            LIMIT 10
        """).fetchall()
        return [{"directory": r[0], "file_count": r[1]} for r in rows]
    except Exception as e:
        logger.error(f"ホットスポット検出失敗: {e}")
        return []


def _detect_entry_points() -> list:
    """主要なエントリーポイントとその依存先を検出"""
    tracer = _get_tracer()
    if tracer is None:
        return []

    try:
        # main, __main__, start, run 等のシンボルを検索
        entry_names = ["main", "__main__", "start", "run", "app", "serve"]
        placeholders = ", ".join(["?" for _ in entry_names])
        rows = tracer.conn.execute(f"""
            SELECT s.name, s.file_path, s.type
            FROM symbols s
            WHERE s.name IN ({placeholders})
        """, entry_names).fetchall()

        entries = []
        for name, fpath, stype in rows:
            # このエントリーポイントが呼び出しているモジュールを取得
            deps = tracer.conn.execute("""
                SELECT DISTINCT callee_name FROM calls WHERE caller_name = ? LIMIT 10
            """, (name,)).fetchall()
            entries.append({
                "name": name,
                "file": fpath,
                "type": stype,
                "dependencies": [d[0] for d in deps]
            })
        return entries
    except Exception as e:
        logger.error(f"エントリーポイント検出失敗: {e}")
        return []


# ============================================================
# MCP Resource: brownie://repo/context
# ============================================================
@mcp.resource("brownie://repo/context")
async def repo_context() -> str:
    """WDCA (Deep Context Awareness) によって生成された最新のプロジェクト要約。
    Agent の初動でリポジトリの全体像を把握するために使用されます。
    """
    summary = await asyncio.to_thread(_build_repo_summary)
    return json.dumps(summary, ensure_ascii=False, indent=2)


# ============================================================
# サーバー起動エントリーポイント
# ============================================================
def _init_from_args():
    """コマンドライン引数からグローバル状態を初期化"""
    global _repo_path, _memory_path, _repo_name

    if len(sys.argv) < 4:
        print("Usage: python -m src.mcp.knowledge_server <repo_path> <memory_path> <repo_name>", file=sys.stderr)
        sys.exit(1)

    _repo_path = os.path.realpath(sys.argv[1])
    _memory_path = os.path.realpath(sys.argv[2])
    _repo_name = sys.argv[3]

    # 環境変数からのオーバーライド（Orchestrator との連携用）
    _repo_path = os.environ.get("BROWNIE_REPO_PATH", _repo_path)
    _repo_name = os.environ.get("BROWNIE_TARGET_REPO", _repo_name)
    _memory_path = os.environ.get("BROWNIE_MEMORY_PATH", _memory_path)

    if not os.path.isdir(_repo_path):
        print(f"Error: repo_path '{_repo_path}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Knowledge Server initialized: repo={_repo_name}, path={_repo_path}")

if __name__ == "__main__":
    _init_from_args()
    mcp.run(transport="stdio")
