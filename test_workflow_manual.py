#!/usr/bin/env python3
"""Manual workflow testing script.

Run: uv run python test_workflow_manual.py
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.workflow import Workflow, WorkflowStore, WorkflowStep, execute_workflow


async def test_workflow_basic():
    """Test basic workflow operations."""
    print("=" * 50)
    print("Workflow Test")
    print("=" * 50)

    # Create store
    store = WorkflowStore()

    # Test 1: List workflows
    print("\n1. Listing workflows...")
    workflows = store.list()
    for wf in workflows:
        print(f"   - {wf.name}: {wf.description}")
        print(f"     Steps: {len(wf.steps)}")

    # Test 2: Get specific workflow
    print("\n2. Loading 'hello-test' workflow...")
    workflow = store.get("hello-test")
    if workflow:
        print(f"   Name: {workflow.name}")
        print(f"   Description: {workflow.description}")
        print(f"   Steps:")
        for i, step in enumerate(workflow.steps, 1):
            print(f"     {i}. {step.tool}: {step.args}")
    else:
        print("   ERROR: Workflow not found!")
        return

    # Test 3: Execute workflow
    print("\n3. Executing workflow...")

    async def mock_execute(tool: str, args: str) -> dict:
        """Mock executor for testing."""
        print(f"   → Executing: {tool} with args='{args}'")

        # Simulate execution
        if tool == "bash":
            import subprocess
            try:
                result = subprocess.run(
                    args,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=Path.home() / ".heare" / "workspace"
                )
                return {
                    "success": result.returncode == 0,
                    "output": result.stdout.strip(),
                    "error": result.stderr.strip() if result.stderr else None
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif tool == "write":
            # Format: "path: content"
            if ":" not in args:
                return {"success": False, "error": "Invalid format"}
            path, content = args.split(":", 1)
            full_path = Path.home() / ".heare" / "workspace" / path.strip()
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content.strip())
            return {"success": True, "output": f"Written to {full_path}"}

        return {"success": False, "error": f"Unknown tool: {tool}"}

    results = await execute_workflow(workflow, mock_execute)

    print("\n4. Results:")
    for r in results:
        status = "✓" if r["success"] else "✗"
        print(f"   {status} Step {r['step']} ({r['tool']}):")
        if r.get("output"):
            print(f"      Output: {r['output'][:100]}")
        if r.get("error"):
            print(f"      Error: {r['error'][:100]}")

    # Test 4: Create a new workflow programmatically
    print("\n5. Creating a new workflow...")
    new_workflow = Workflow(
        name="test-echo",
        description="Echo test",
        steps=[
            WorkflowStep(tool="bash", args="echo 'Test 1'"),
            WorkflowStep(tool="bash", args="echo 'Test 2'"),
            WorkflowStep(tool="bash", args="echo 'Test 3'"),
        ]
    )
    store.save(new_workflow)
    print(f"   Created: {new_workflow.name}")

    # Test 6: Delete test workflow
    print("\n6. Cleanup...")
    store.delete("test-echo")
    print("   Deleted test-echo")

    print("\n" + "=" * 50)
    print("Test complete!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_workflow_basic())
