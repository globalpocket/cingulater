import pytest
import asyncio
from pathlib import Path
from src.core.workflow_manager import WorkflowLoader
import yaml
import os

@pytest.fixture
def temp_workflow_setup(tmp_path):
    project_root = tmp_path / "project"
    workspace_root = tmp_path / "workspace"
    
    project_root.mkdir()
    workspace_root.mkdir()
    
    # Core workflows
    core_wf_dir = project_root / "workflows"
    core_wf_dir.mkdir()
    
    # YAML workflow
    wf_yaml = core_wf_dir / "test_flow.yaml"
    wf_yaml.write_text("""
description: "A test workflow"
start_node: "node1"
nodes:
  node1:
    prompt_file: "node1.md"
    next: "END"
""", encoding="utf-8")
    
    # MD prompt
    node1_md = core_wf_dir / "node1.md"
    node1_md.write_text("You are a tester. Say HELLO.", encoding="utf-8")
    
    return project_root, workspace_root

@pytest.mark.asyncio
async def test_workflow_execution_compilation(temp_workflow_setup):
    project_root, workspace_root = temp_workflow_setup
    
    loader = WorkflowLoader(project_root, workspace_root)
    # Mock config
    config = {
        "llm": {
            "models": {"planner": "mock-model", "executor": "mock-model"},
            "planner_endpoint": "http://localhost:8080",
            "executor_endpoint": "http://localhost:8080"
        }
    }
    
    tools = loader.load_all(config=config)
    assert "test_flow" in tools
    
    # Note: We won't actually call the LLM in this unit test to avoid external dependencies,
    # but we will check if the callable was created and has metadata.
    callable_tool = tools["test_flow"]
    assert callable_tool.__name__ == "test_flow"
    assert "test workflow" in callable_tool.__doc__.lower()

def test_dynamic_workflow_state_definition():
    from src.core.workflow_manager import DynamicWorkflowState
    state = DynamicWorkflowState(
        messages=[],
        input_data={},
        current_status="init",
        results={}
    )
    assert state["current_status"] == "init"
