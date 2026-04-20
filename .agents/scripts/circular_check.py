import ast
import os
from pathlib import Path
import networkx as nx
from loguru import logger

def get_imports(file_path, root_dir):
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read())
        except Exception:
            return []
    
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports

def analyze_circular_dependencies(src_path):
    src_path = Path(src_path).resolve()
    g = nx.DiGraph()
    
    for root, _, files in os.walk(src_path):
        for file in files:
            if file.endswith('.py'):
                fpath = Path(root) / file
                rel_path = fpath.relative_to(src_path.parent)
                module_name = str(rel_path).replace('/', '.').replace('.py', '').replace('.__init__', '')
                
                g.add_node(module_name)
                
                imports = get_imports(fpath, src_path)
                for imp in imports:
                    if imp.startswith('src.'):
                        g.add_edge(module_name, imp)
    
    try:
        cycle = nx.find_cycle(g, orientation='original')
        logger.error(f"❌ Circular dependency detected: {cycle}")
        return cycle
    except nx.NetworkXNoCycle:
        logger.info("✅ No circular dependencies detected in src/.")
        return None

if __name__ == "__main__":
    analyze_circular_dependencies("src")
