import pytest
from src.core.knowledge_base import KnowledgeBaseProvider

@pytest.fixture
def provider():
    # KnowledgeBaseProvider は repo_path と repo_name を要求する
    return KnowledgeBaseProvider("/tmp/test_repo", "test_repo")

def test_scan_python_imports(provider):
    content = "import os\nfrom src.core import config"
    # scan_file は内部の tracer が保持している
    provider.tracer.scan_file("test.py", content)
    
    # グラフも tracer が保持している
    edges = list(provider.tracer.graph.edges(data=True))
    targets = [t for s, t, d in edges if d.get("type") == "depends_on"]
    assert "os" in targets
    assert "src.core" in targets

def test_scan_go_imports(provider):
    content = 'package main\nimport "fmt"\nimport "github.com/lib/pq"'
    provider.tracer.scan_file("main.go", content)
    
    edges = list(provider.tracer.graph.edges(data=True))
    targets = [t for s, t, d in edges if d.get("type") == "depends_on"]
    assert '"fmt"' in targets
    assert '"github.com/lib/pq"' in targets

def test_scan_typescript_imports(provider):
    content = 'import { useState } from "react";\nimport "./style.css";'
    provider.tracer.scan_file("app.ts", content)
    
    edges = list(provider.tracer.graph.edges(data=True))
    targets = [t for s, t, d in edges if d.get("type") == "depends_on"]
    assert '"react"' in targets
    assert '"./style.css"' in targets

def test_scan_rust_imports(provider):
    content = 'use std::collections::HashMap;\nuse crate::utils;'
    provider.tracer.scan_file("main.rs", content)
    
    edges = list(provider.tracer.graph.edges(data=True))
    targets = [t for s, t, d in edges if d.get("type") == "depends_on"]
    assert "std::collections::HashMap" in targets
    assert "crate::utils" in targets
