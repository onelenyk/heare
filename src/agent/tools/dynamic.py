"""Execution engine for dynamically created tools.

Provides async functions to execute user-created tools with different
implementation types: bash commands, HTTP fetches, and Python expressions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # pragma: no cover

logger = logging.getLogger("heare.dynamic_tools")


async def execute_bash_tool(command: str, args: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Execute a bash tool with argument substitution.

    Args are substituted into the command using {arg} placeholders.
    The command runs in a subprocess with a 30-second timeout.
    """
    # Substitute {arg} placeholders
    for key, value in args.items():
        placeholder = f"{{{key}}}"
        if placeholder in command:
            command = command.replace(placeholder, str(value))

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode == 0:
            return {
                "success": True,
                "output": stdout.decode().strip(),
            }
        else:
            return {
                "success": False,
                "error": stderr.decode().strip() or "Command failed",
            }
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {"success": False, "error": "Command timed out after 30 seconds"}
    except Exception as e:
        logger.exception("bash tool execution failed")
        return {"success": False, "error": str(e)}


async def execute_fetch_tool(url: str, args: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Execute a fetch tool with argument substitution.

    Args are substituted into the URL using {arg} placeholders.
    Performs an HTTP GET request with a 10-second timeout.
    """
    if httpx is None:
        return {"success": False, "error": "httpx is not installed"}

    # Substitute {arg} placeholders
    for key, value in args.items():
        placeholder = f"{{{key}}}"
        if placeholder in url:
            url = url.replace(placeholder, str(value))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return {
                "success": True,
                "output": response.text,
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        logger.exception("fetch tool execution failed")
        return {"success": False, "error": str(e)}


async def execute_python_tool(
    code: str, args: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Execute a Python expression with args available.

    WARNING: eval is dangerous and should only be used for trusted input.
    The `args` dict is available as a local variable.

    For safety, builtins are restricted and only safe operations are allowed.
    """
    try:
        # WARNING: eval is dangerous, only for trusted use
        # Restrict builtins for basic safety
        result = eval(code, {"__builtins__": {}}, {"args": args})
        return {
            "success": True,
            "output": str(result),
        }
    except Exception as e:
        logger.exception("python tool execution failed")
        return {"success": False, "error": str(e)}


__all__ = [
    "execute_bash_tool",
    "execute_fetch_tool",
    "execute_python_tool",
]
