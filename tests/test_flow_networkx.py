import os
import tempfile

import duckdb
import pytest
from src.workspace.analyzer.flow import FlowTracer


@pytest.fixture
def temp_db():
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, "test_index.db")

    conn = duckdb.connect(path)
    conn.execute("CREATE TABLE symbols (name TEXT, type TEXT, file_path TEXT)")
    conn.execute(
        "CREATE TABLE calls (caller_name TEXT, callee_name TEXT, "
        "file_path TEXT, line INTEGER)"
    )

    # Mock Symbols
    conn.execute("INSERT INTO symbols VALUES ('main', 'function', 'main.py')")
    conn.execute("INSERT INTO symbols VALUES ('process_data', 'function', 'utils.py')")
    conn.execute("INSERT INTO symbols VALUES ('save_to_db', 'function', 'db.py')")
    conn.execute(
        "INSERT INTO symbols VALUES ('validate_input', 'function', 'utils.py')"
    )
    conn.execute("INSERT INTO symbols VALUES ('log_event', 'function', 'logger.py')")

    # Mock Calls
    conn.execute("INSERT INTO calls VALUES ('main', 'process_data', 'main.py', 10)")
    conn.execute(
        "INSERT INTO calls VALUES ('process_data', 'validate_input', 'utils.py', 20)"
    )
    conn.execute(
        "INSERT INTO calls VALUES ('process_data', 'save_to_db', 'utils.py', 25)"
    )
    conn.execute(
        "INSERT INTO calls VALUES ('process_data', 'log_event', 'utils.py', 30)"
    )
    conn.execute("INSERT INTO calls VALUES ('save_to_db', 'log_event', 'db.py', 15)")

    conn.close()
    yield path
    if os.path.exists(path):
        os.remove(path)
    os.rmdir(tmp_dir)


def test_flow_tracer_networkx(temp_db):
    tracer = FlowTracer(temp_db)
    tracer.build_graph()

    # 1. Check graph nodes and edges
    assert "main" in tracer.graph.nodes
    assert "process_data" in tracer.graph.nodes
    assert tracer.graph.has_edge("main", "process_data")
    assert tracer.graph.has_edge("process_data", "validate_input")

    # 2. Check critical dependencies (Top-K)
    # process_data has out-degree 3, save_to_db has 1, main has 1.
    critical = tracer.get_critical_dependencies(top_k=2)
    assert len(critical) == 2
    assert critical[0]["symbol"] == "process_data"

    # 3. Check trace_flow (Mermaid)
    mermaid = tracer.trace_flow("main")
    assert "sequenceDiagram" in mermaid
    assert "main->>+ process_data" in mermaid
    assert "process_data->>+ validate_input" in mermaid

    tracer.close()


def test_critical_dependencies_metrics(temp_db):
    tracer = FlowTracer(temp_db)
    tracer.build_graph()

    critical = tracer.get_critical_dependencies(top_k=5)

    # 'process_data' should have high out_degree
    pd_info = next(item for item in critical if item["symbol"] == "process_data")
    assert pd_info["out_degree"] == 3

    # 'log_event' should have 0 out_degree
    le_info = next(item for item in critical if item["symbol"] == "log_event")
    assert le_info["out_degree"] == 0

    tracer.close()
