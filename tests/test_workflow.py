"""Tests for workflow system."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.workflow import Workflow, WorkflowStep, WorkflowStore, execute_workflow


@pytest.fixture
def temp_store():
    """Create a temporary workflow store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Create a mock Settings with db_path
        store = WorkflowStore(type("Settings", (), {"db_path": tmp_path / "heare.db"})())
        yield store


def test_workflow_to_dict():
    wf = Workflow(
        name="test",
        description="Test workflow",
        steps=[
            WorkflowStep(tool="bash", args="echo hello"),
            WorkflowStep(tool="bash", args="echo world"),
        ],
    )
    data = wf.to_dict()
    assert data["name"] == "test"
    assert data["description"] == "Test workflow"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["tool"] == "bash"
    assert data["steps"][0]["args"] == "echo hello"


def test_workflow_from_dict():
    data = {
        "name": "test",
        "description": "Test workflow",
        "steps": [
            {"tool": "bash", "args": "echo hello"},
            {"tool": "bash", "args": "echo world"},
        ],
    }
    wf = Workflow.from_dict(data)
    assert wf.name == "test"
    assert wf.description == "Test workflow"
    assert len(wf.steps) == 2
    assert wf.steps[0].tool == "bash"
    assert wf.steps[0].args == "echo hello"


def test_store_save_and_get(temp_store):
    wf = Workflow(
        name="my-workflow",
        description="My test workflow",
        steps=[WorkflowStep(tool="bash", args="echo test")],
    )
    assert temp_store.save(wf)

    retrieved = temp_store.get("my-workflow")
    assert retrieved is not None
    assert retrieved.name == "my-workflow"
    assert retrieved.description == "My test workflow"
    assert len(retrieved.steps) == 1


def test_store_list(temp_store):
    wf1 = Workflow(name="wf1", description="First", steps=[])
    wf2 = Workflow(name="wf2", description="Second", steps=[])
    temp_store.save(wf1)
    temp_store.save(wf2)

    workflows = temp_store.list()
    assert len(workflows) == 2
    names = {w.name for w in workflows}
    assert names == {"wf1", "wf2"}


def test_store_delete(temp_store):
    wf = Workflow(name="delete-me", description="Delete this", steps=[])
    temp_store.save(wf)
    assert temp_store.get("delete-me") is not None

    assert temp_store.delete("delete-me")
    assert temp_store.get("delete-me") is None


def test_store_get_nonexistent(temp_store):
    assert temp_store.get("nonexistent") is None


def test_store_delete_nonexistent(temp_store):
    assert not temp_store.delete("nonexistent")


@pytest.mark.asyncio
async def test_execute_workflow():
    results = []

    async def mock_execute(tool, args):
        results.append((tool, args))
        return {"success": True, "output": f"Executed {tool}"}

    wf = Workflow(
        name="test",
        description="Test",
        steps=[
            WorkflowStep(tool="bash", args="echo step1"),
            WorkflowStep(tool="bash", args="echo step2"),
        ],
    )

    workflow_results = await execute_workflow(wf, mock_execute)

    assert len(results) == 2
    assert results[0] == ("bash", "echo step1")
    assert results[1] == ("bash", "echo step2")

    assert len(workflow_results) == 2
    assert workflow_results[0]["success"] is True
    assert workflow_results[1]["success"] is True


@pytest.mark.asyncio
async def test_execute_workflow_with_failure():
    results = []

    async def mock_execute(tool, args):
        results.append((tool, args))
        if "fail" in args:
            return {"success": False, "error": "Simulated failure"}
        return {"success": True, "output": "OK"}

    wf = Workflow(
        name="test",
        description="Test",
        steps=[
            WorkflowStep(tool="bash", args="echo ok"),
            WorkflowStep(tool="bash", args="echo fail"),
            WorkflowStep(tool="bash", args="echo after"),
        ],
    )

    workflow_results = await execute_workflow(wf, mock_execute)

    # All steps run even after failure
    assert len(results) == 3
    assert workflow_results[0]["success"] is True
    assert workflow_results[1]["success"] is False
    assert workflow_results[2]["success"] is True


def test_workflow_with_dict_args():
    """Test workflow steps with dict arguments (for MCP tools)."""
    data = {
        "name": "mcp-test",
        "description": "Test MCP workflow",
        "steps": [
            {"tool": "bash", "args": "echo simple"},
            {
                "tool": "mcp__chrome__navigate",
                "args": {"url": "https://example.com"},
            },
        ],
    }
    wf = Workflow.from_dict(data)
    assert len(wf.steps) == 2
    assert wf.steps[0].args == "echo simple"
    assert wf.steps[1].args == {"url": "https://example.com"}
