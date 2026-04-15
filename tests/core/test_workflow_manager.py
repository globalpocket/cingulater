import logging

import pytest

from src.core.workflow_manager import WorkflowLoader


def test_workflow_loader_priority(tmp_path):
    # Core スコープの作成
    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    (core_wf / "tool_a.yaml").write_text("description: core yaml\nsteps: []")
    (core_wf / "tool_b.md").write_text("# core prompt")

    # Workspace スコープの作成
    workspace_root = tmp_path / "workspace"
    workspace_wf = workspace_root / ".brwn" / "workflows"
    workspace_wf.mkdir(parents=True)
    (workspace_wf / "tool_a.yaml").write_text("description: workspace yaml\nsteps: []")
    (workspace_wf / "tool_b.yaml").write_text(
        "description: workspace yaml b\nsteps: []"
    )
    (workspace_wf / "tool_c.md").write_text("# workspace prompt c")

    loader = WorkflowLoader(project_root=tmp_path, workspace_root=workspace_root)

    # MCPツール名の登録
    mcp_tools = ["tool_mcp"]
    tools = loader.load_all(mcp_tool_names=mcp_tools)

    # 検証
    assert "tool_a" in tools
    assert "tool_b" in tools
    assert "tool_c" in tools

    # tool_a は Core YAML が優先されるはず (先勝ち実装)
    assert "core yaml" in tools["tool_a"].__doc__

    # tool_b は Core MD よりも Workspace YAML が優先されるはず (YAML > MD)
    # 実際の実装では YAML 全走査の後に MD 全走査なので、
    # Core MD が後から来ても YAML が既に登録されていればスキップされる
    assert "workspace yaml b" in tools["tool_b"].__doc__


def test_mcp_priority(tmp_path):
    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    (core_wf / "duplicate.yaml").write_text("description: core yaml\nsteps: []")

    loader = WorkflowLoader(project_root=tmp_path)
    # MCP と重複
    tools = loader.load_all(mcp_tool_names=["duplicate"])

    assert "duplicate" not in tools


def test_circular_reference(tmp_path):
    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    # ファイル名 "recursion" が本文に含まれている
    (core_wf / "recursion.yaml").write_text("description: test\ncall: recursion")

    loader = WorkflowLoader(project_root=tmp_path)

    with pytest.raises(ValueError, match="Circular reference detected"):
        loader.load_all()


def test_metadata_parsing(tmp_path):
    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    (core_wf / "meta.yaml").write_text(
        "description: This is a test tool\n"
        "triggers:\n"
        "  - cron: '0 0 * * *'\n"
        "  - event: 'push'"
    )

    loader = WorkflowLoader(project_root=tmp_path)
    tools = loader.load_all()

    fn = tools["meta"]
    assert fn.__doc__ == "This is a test tool"
    assert hasattr(fn, "triggers")
    assert fn.triggers == [{"cron": "0 0 * * *"}, {"event": "push"}]


@pytest.mark.asyncio
async def test_stub_callable(tmp_path):
    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    (core_wf / "test_wf.yaml").write_text("description: test")
    (core_wf / "test_prompt.md").write_text("# test prompt body")

    loader = WorkflowLoader(project_root=tmp_path)
    tools = loader.load_all()

    # YAML スタブの呼び出し
    res_yaml = await tools["test_wf"](arg1="val1")
    assert "Executing workflow test_wf" in res_yaml
    assert "'arg1': 'val1'" in res_yaml

    # MD スタブの呼び出し
    res_md = await tools["test_prompt"]()
    assert "Prompt helper 'test_prompt' called" in res_md


def test_warning_on_duplicate(tmp_path, caplog):
    caplog.set_level(logging.WARNING)

    core_wf = tmp_path / "workflows"
    core_wf.mkdir()
    (core_wf / "dup.yaml").write_text("description: core")

    workspace_root = tmp_path / "workspace"
    workspace_wf = workspace_root / ".brwn" / "workflows"
    workspace_wf.mkdir(parents=True)
    (workspace_wf / "dup.yaml").write_text("description: workspace")

    loader = WorkflowLoader(project_root=tmp_path, workspace_root=workspace_root)
    loader.load_all()

    assert "Skipping yaml tool 'dup' from workspace" in caplog.text
