"""Workflow system for saving and reusing action sequences.

Workflows live in ~/.heare/workflows/*.json and can be invoked by name.
Format:
{
  "name": "my-workflow",
  "description": "Does something useful",
  "steps": [
    {"tool": "bash", "args": "echo 'step 1'"},
    {"tool": "bash", "args": "echo 'step 2'"}
  ]
}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger("heare.workflow")


@dataclass
class WorkflowStep:
    """Single step in a workflow."""

    tool: str
    args: str | dict


@dataclass
class Workflow:
    """A reusable sequence of actions."""

    name: str
    description: str
    steps: list[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [{"tool": s.tool, "args": s.args} for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        steps = [
            WorkflowStep(tool=s["tool"], args=s["args"])
            for s in data.get("steps", [])
        ]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            steps=steps,
        )


class WorkflowStore:
    """Manages workflow persistence."""

    def __init__(self, settings: Settings | None = None):
        if settings:
            self._dir = settings.db_path.parent / "workflows"
        else:
            self._dir = Path.home() / ".heare" / "workflows"
        self._dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Workflow]:
        """List all saved workflows."""
        workflows = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                workflows.append(Workflow.from_dict(data))
            except Exception as e:
                logger.warning("Failed to load workflow %s: %s", path, e)
        return workflows

    def get(self, name: str) -> Workflow | None:
        """Get a workflow by name."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Workflow.from_dict(data)
        except Exception as e:
            logger.error("Failed to load workflow %s: %s", name, e)
            return None

    def save(self, workflow: Workflow) -> bool:
        """Save a workflow."""
        path = self._dir / f"{workflow.name}.json"
        try:
            path.write_text(json.dumps(workflow.to_dict(), indent=2) + "\n")
            logger.info("Saved workflow: %s", workflow.name)
            return True
        except Exception as e:
            logger.error("Failed to save workflow %s: %s", workflow.name, e)
            return False

    def delete(self, name: str) -> bool:
        """Delete a workflow."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            logger.info("Deleted workflow: %s", name)
            return True
        except Exception as e:
            logger.error("Failed to delete workflow %s: %s", name, e)
            return False


async def execute_workflow(
    workflow: Workflow,
    execute_action: callable,
) -> list[dict]:
    """Execute a workflow step by step.

    Args:
        workflow: The workflow to execute
        execute_action: Async callable(tool, args) -> result dict

    Returns:
        List of results from each step
    """
    results = []
    for i, step in enumerate(workflow.steps, 1):
        logger.info(
            "[WORKFLOW %s step %d/%d] tool=%s",
            workflow.name,
            i,
            len(workflow.steps),
            step.tool,
        )
        try:
            # Convert args to string if it's a dict (for MCP tools)
            if isinstance(step.args, dict):
                args_str = json.dumps(step.args)
            else:
                args_str = str(step.args)

            result = await execute_action(step.tool, args_str)
            results.append({
                "step": i,
                "tool": step.tool,
                "success": result.get("success", True),
                "output": result.get("output", ""),
                "error": result.get("error"),
            })

            # Stop on failure? For now, continue anyway
            if not result.get("success", True):
                logger.warning(
                    "[WORKFLOW %s step %d] failed: %s",
                    workflow.name,
                    i,
                    result.get("error"),
                )
        except Exception as e:
            logger.exception(
                "[WORKFLOW %s step %d] exception: %s",
                workflow.name,
                i,
                e,
            )
            results.append({
                "step": i,
                "tool": step.tool,
                "success": False,
                "output": "",
                "error": str(e),
            })

    return results
