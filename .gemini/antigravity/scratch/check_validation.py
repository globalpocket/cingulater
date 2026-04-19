import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any

# WorkflowDefinition のスキーマ（src/core/workflow_manager.py より抜粋）
class WorkflowNodeDefinition(BaseModel):
    prompt_file: str
    next: str
    model: str = "planner"

class WorkflowDefinition(BaseModel):
    name: str
    description: Optional[str] = None
    start_node: str
    nodes: Dict[str, WorkflowNodeDefinition]
    triggers: List[Dict[str, Any]] = Field(default_factory=list)

def check_workflows():
    workflow_dir = Path("workflows")
    print(f"Scanning directory: {workflow_dir.absolute()}")
    
    if not workflow_dir.exists():
        print("Directory does not exist!")
        return

    for file_path in workflow_dir.glob("*.yaml"):
        print(f"\n--- Checking: {file_path.name} ---")
        try:
            content = file_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                print("Skipping: Not a dictionary")
                continue
            
            # バリデーション試行
            try:
                WorkflowDefinition(**data)
                print("✅ VALID WorkflowDefinition")
            except Exception as e:
                print(f"❌ INVALID: {e}")
                
        except Exception as e:
            print(f"FAILED to read/parse YAML: {e}")

if __name__ == "__main__":
    check_workflows()
